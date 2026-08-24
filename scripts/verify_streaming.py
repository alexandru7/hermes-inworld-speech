#!/usr/bin/env python3
"""Verify Inworld streaming TTS against a live endpoint.

The unit suite mocks HTTP, so it cannot tell you how Inworld actually frames
streamed chunks for each encoding.  This script does: it streams real audio,
reports time-to-first-chunk, checks for corruption at chunk boundaries, and
writes a file you can play.

    export INWORLD_API_KEY='...'
    python scripts/verify_streaming.py

Two checks matter, and both are narrower than they first appear:

* **Repeated file headers.**  Only a *file-level* signature repeating is a
  fault.  Container formats legitimately repeat per-page or per-frame markers —
  every Ogg page starts with ``OggS`` by design, and every MP3 frame starts with
  a sync word.  Flagging those would be a false alarm.

* **Discontinuities at chunk boundaries.**  Stray header bytes left inside the
  audio decode as a click.  Speech is full of genuine transients, so a raw
  count means nothing; what matters is whether the jumps land *on the chunk
  boundaries*.  This is only measurable for uncompressed output, where byte
  offsets map to sample offsets.
"""

from __future__ import annotations

import argparse
import array
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Hermes supplies these at runtime; stub them so the plugin imports standalone.
for _name, _base in (
    ("agent.tts_provider", "TTSProvider"),
    ("agent.transcription_provider", "TranscriptionProvider"),
):
    sys.modules.setdefault("agent", types.ModuleType("agent"))
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        setattr(_mod, _base, type(_base, (), {}))
        sys.modules[_name] = _mod

from hermes_inworld_speech import providers  # noqa: E402
from hermes_inworld_speech.client import api_root  # noqa: E402

DEFAULT_FORMATS = ["mp3", "wav", "ogg", "opus", "flac", "pcm", "linear16"]
DEFAULT_TEXT = (
    "Streaming verification. If you can hear this sentence all the way through "
    "without clicks or gaps, the chunk handling for this encoding is correct."
)

# Uncompressed 16-bit output, where a byte offset maps to a sample offset.
_UNCOMPRESSED = {"wav", "pcm", "linear16"}

# File-level signatures.  A repeat of one of these mid-stream is a real fault.
# Per-page (OggS) and per-frame (MP3 sync, FLAC frame sync) markers are NOT
# listed here: repeating those is how the containers are supposed to work.
_FILE_SIGNATURES = {
    b"OpusHead": "Opus file header",
    b"fLaC": "FLAC file header",
    b"ID3": "ID3 tag",
}

_EXTENSIONS = {"pcm": ".pcm", "linear16": ".wav", "opus": ".ogg", "ogg": ".ogg"}


def _describe_head(chunk: bytes) -> str:
    """Label the head of a chunk, distinguishing file from page/frame markers."""

    if chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE":
        return "RIFF/WAV file header"
    if chunk[:4] == b"OggS":
        return "Ogg page" + (" + OpusHead" if b"OpusHead" in chunk[:64] else "")
    if chunk[:4] == b"fLaC":
        return "FLAC file header"
    if chunk[:3] == b"ID3":
        return "ID3 tag"
    if chunk[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "MP3 frame"
    if chunk[:2] == b"\xff\xf8":
        return "FLAC frame"
    return "raw/unrecognized"


def _repeated_file_headers(chunks: list[bytes]) -> list[tuple[int, str]]:
    """Find file-level headers on any chunk after the first."""

    found = []
    for index, chunk in enumerate(chunks[1:], start=1):
        if chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE":
            found.append((index, "RIFF/WAV file header"))
            continue
        for magic, label in _FILE_SIGNATURES.items():
            # Look only at the head: OpusHead deeper inside a page is normal.
            if chunk[:64].startswith(magic) or (magic == b"OpusHead" and magic in chunk[:64]):
                found.append((index, label))
                break
    return found


def _boundary_report(chunks: list[bytes], threshold: int = 6000) -> str:
    """Compare discontinuities on chunk boundaries against the rest of the audio.

    Header bytes left inside the samples produce a jump exactly where one chunk
    meets the next.  Ordinary speech transients are scattered, so the boundary
    rate standing far above the background rate is the signal.
    """

    audio = b"".join(chunks)
    offset = 44 if (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE") else 0
    body = audio[offset:]
    body = body[: len(body) // 2 * 2]

    samples = array.array("h")
    samples.frombytes(body)
    if len(samples) < 16:
        return "too short to analyse"

    # Sample index where each chunk hands over to the next.
    boundaries, running = [], -offset
    for chunk in chunks[:-1]:
        running += len(chunk)
        if running > 0:
            boundaries.append(running // 2)
    boundaries = [b for b in boundaries if 1 <= b < len(samples) - 1]
    if not boundaries:
        return "no interior boundaries"

    near = set()
    for b in boundaries:
        near.update(range(max(1, b - 2), min(len(samples), b + 3)))

    on_boundary = sum(1 for i in near if abs(samples[i] - samples[i - 1]) > threshold)
    elsewhere = sum(
        1
        for i in range(1, len(samples))
        if i not in near and abs(samples[i] - samples[i - 1]) > threshold
    )
    background = elsewhere / max(1, len(samples) - len(near))
    expected = background * len(near)

    verdict = "clean"
    if on_boundary >= max(2, len(boundaries) * 0.5) and on_boundary > expected * 4:
        verdict = "CLICKS AT BOUNDARIES"
    return (
        f"{verdict} ({on_boundary} jump(s) on {len(boundaries)} boundaries, "
        f"{expected:.1f} expected from speech)"
    )


def _decode_check(path: Path, fmt: str, sample_rate: int) -> str:
    if not shutil.which("ffmpeg"):
        return "skipped (ffmpeg not installed)"
    cmd = ["ffmpeg", "-v", "error"]
    if fmt == "pcm":
        # Raw samples carry no header; ffmpeg needs to be told the layout.
        cmd += ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1"]
    cmd += ["-i", str(path), "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and not proc.stderr.strip():
        return "OK"
    return "FAILED: " + (proc.stderr.strip().splitlines() or ["non-zero exit"])[0]


def verify(fmt: str, text: str, voice: str | None, model: str | None, out_dir: Path) -> bool:
    provider = providers.InworldTTSProvider()
    encoding = providers._STREAM_FORMATS[fmt]
    print(f"\n\033[1m{fmt}\033[0m  (audioEncoding={encoding})")

    started = time.monotonic()
    first_at: float | None = None
    chunks: list[bytes] = []
    try:
        for chunk in provider.stream(text, voice=voice, model=model, format=fmt):
            if first_at is None:
                first_at = time.monotonic() - started
            chunks.append(chunk)
    except NotImplementedError as exc:
        print(f"  streaming unsupported: {exc}")
        return True
    except Exception as exc:
        print(f"  \033[31mFAILED\033[0m: {type(exc).__name__}: {exc}")
        return False

    total = time.monotonic() - started
    audio = b"".join(chunks)
    ok = True

    print(f"  chunks            {len(chunks)}")
    print(f"  bytes             {len(audio):,}")
    print(f"  first chunk       {first_at * 1000:.0f} ms")
    print(f"  total             {total * 1000:.0f} ms")
    print(f"  first chunk head  {_describe_head(chunks[0])}")

    repeats = _repeated_file_headers(chunks)
    if repeats:
        shown = ", ".join(f"#{i} {label}" for i, label in repeats[:5])
        print(f"  \033[31mrepeated file headers\033[0m  {len(repeats)} ({shown})")
        print(f"  \033[31m-> add {encoding!r} to _PER_CHUNK_RIFF_ENCODINGS\033[0m")
        ok = False
    else:
        print("  repeated file headers  none")

    path = out_dir / f"stream_{fmt}{_EXTENSIONS.get(fmt, '.' + fmt)}"
    path.write_bytes(audio)

    rate = providers._sample_rate(providers._provider_config("tts"))
    verdict = _decode_check(path, fmt, rate)
    colour = "32" if verdict == "OK" else ("33" if verdict.startswith("skipped") else "31")
    print(f"  decode            \033[{colour}m{verdict}\033[0m")
    if verdict.startswith("FAILED"):
        ok = False

    if fmt in _UNCOMPRESSED:
        boundary = _boundary_report(chunks)
        bad = boundary.startswith("CLICKS")
        print(f"  chunk boundaries  \033[{'31' if bad else '32'}m{boundary}\033[0m")
        if bad:
            ok = False
    else:
        # Compressed frames do not map byte offsets to sample offsets, so a
        # boundary analysis is not meaningful; the decode check covers this.
        print("  chunk boundaries  n/a (compressed) - listen to confirm")

    print(f"  wrote             {path}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--formats", nargs="+", default=DEFAULT_FORMATS, choices=DEFAULT_FORMATS)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("./streaming-check"))
    args = parser.parse_args()

    if not os.getenv("INWORLD_API_KEY"):
        print("INWORLD_API_KEY is not set.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"endpoint  {api_root()}/tts/v1/voice:stream")
    print(f"text      {len(args.text)} chars")

    results = {f: verify(f, args.text, args.voice, args.model, args.out_dir) for f in args.formats}

    failed = [f for f, ok in results.items() if not ok]
    print("\n" + "─" * 58)
    if failed:
        print(f"\033[31m{len(failed)} format(s) need attention: {', '.join(failed)}\033[0m")
        return 1
    print(f"\033[32mall {len(results)} format(s) streamed cleanly\033[0m")
    print("Play the files before trusting this: automated checks catch structural")
    print("corruption, not every artefact a human ear would notice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
