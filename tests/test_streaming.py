"""Streaming TTS: NDJSON framing, per-encoding headers, and fallback signalling."""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from urllib.error import HTTPError

import pytest

from hermes_inworld_speech import client as inworld_client
from hermes_inworld_speech import providers

RIFF = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32  # 44 bytes


def _line(audio: bytes) -> bytes:
    payload = {"result": {"audioContent": base64.b64encode(audio).decode()}}
    return json.dumps(payload).encode() + b"\n"


class _FakeStream:
    """A socket-ish response that hands out bytes in fixed-size blocks."""

    def __init__(self, body: bytes, block: int = 8):
        self.body = body
        self.block = block
        self.pos = 0
        self.closed = False

    def read(self, size=None):
        take = self.block if size is None else min(size, self.block)
        chunk = self.body[self.pos : self.pos + take]
        self.pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True
        return False


def _client_streaming(monkeypatch, body: bytes, block: int = 8):
    stream = _FakeStream(body, block)
    monkeypatch.setattr(inworld_client.InworldClient, "open_stream", lambda *a, **k: stream)
    return stream


# --- NDJSON framing ----------------------------------------------------------


def test_line_split_across_reads(monkeypatch):
    """The common real case: one base64 line spans many socket reads."""

    body = _line(b"first") + _line(b"second") + _line(b"third")
    _client_streaming(monkeypatch, body, block=7)

    got = list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))
    assert [base64.b64decode(m["result"]["audioContent"]) for m in got] == [
        b"first",
        b"second",
        b"third",
    ]


def test_final_line_without_trailing_newline(monkeypatch):
    body = _line(b"a") + json.dumps({"result": {"audioContent": "Yg=="}}).encode()
    _client_streaming(monkeypatch, body, block=5)

    got = list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))
    assert len(got) == 2
    assert base64.b64decode(got[1]["result"]["audioContent"]) == b"b"


def test_blank_and_whitespace_lines_are_skipped(monkeypatch):
    body = b"\n" + _line(b"a") + b"   \n\n" + _line(b"b") + b"\n"
    _client_streaming(monkeypatch, body, block=4)

    got = list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))
    assert len(got) == 2


def test_malformed_line_raises_clearly(monkeypatch):
    _client_streaming(monkeypatch, _line(b"a") + b"{not json}\n", block=16)

    with pytest.raises(RuntimeError, match="malformed JSON line"):
        list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))


def test_non_object_line_is_rejected(monkeypatch):
    _client_streaming(monkeypatch, b"[1,2,3]\n", block=16)

    with pytest.raises(RuntimeError, match="unexpected JSON line shape"):
        list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))


def test_in_band_error_object_raises(monkeypatch):
    """The stream can fail mid-body with HTTP 200 already sent."""

    body = _line(b"a") + json.dumps({"error": {"message": "quota exceeded"}}).encode() + b"\n"
    _client_streaming(monkeypatch, body, block=16)

    with pytest.raises(RuntimeError, match="quota exceeded"):
        list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))


def test_stream_response_is_closed(monkeypatch):
    stream = _client_streaming(monkeypatch, _line(b"a"), block=64)
    list(inworld_client.InworldClient().request_ndjson("POST", "/x", {}))
    assert stream.closed


# --- retry policy ------------------------------------------------------------


def test_open_stream_retries_until_the_response_opens(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "k")
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError("u", 503, "Service Unavailable", {}, BytesIO(b"down"))
        return _FakeStream(_line(b"ok"), block=64)

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    got = list(inworld_client.InworldClient(max_attempts=3).request_ndjson("POST", "/x", {}))
    assert len(calls) == 3
    assert len(got) == 1


def test_no_retry_once_bytes_have_been_yielded(monkeypatch):
    """Replaying a partially consumed stream would duplicate audio."""

    monkeypatch.setenv("INWORLD_API_KEY", "k")
    calls = []

    class _Failing(_FakeStream):
        def read(self, size=None):
            if self.pos >= len(self.body):
                raise OSError("connection reset mid-stream")
            return super().read(size)

    def fake_urlopen(request, timeout):
        calls.append(1)
        return _Failing(_line(b"a"), block=64)

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    with pytest.raises(OSError, match="connection reset"):
        list(inworld_client.InworldClient(max_attempts=3).request_ndjson("POST", "/x", {}))
    assert len(calls) == 1, "must not re-request after delivering a chunk"


def test_non_retryable_status_is_not_retried(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "k")
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise HTTPError("u", 400, "Bad Request", {}, BytesIO(b"bad voice"))

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Inworld HTTP 400"):
        list(inworld_client.InworldClient(max_attempts=3).request_ndjson("POST", "/x", {}))
    assert len(calls) == 1


# --- eager NotImplementedError (the generator trap) --------------------------


@pytest.fixture
def tts_cfg(monkeypatch):
    def _apply(**values):
        monkeypatch.setattr(
            providers,
            "_provider_config",
            lambda section: dict(values) if section == "tts" else {},
        )

    return _apply


def test_unsupported_format_raises_without_iterating(tts_cfg):
    """Hermes needs the raise at call time so it can fall back to synthesize().

    Calling stream() and never touching the result is the whole point: a
    generator function would not raise until first iteration.
    """

    tts_cfg()
    with pytest.raises(NotImplementedError, match="does not support format 'aiff'"):
        providers.InworldTTSProvider().stream("hi", format="aiff")


def test_kill_switch_raises_without_iterating(tts_cfg):
    tts_cfg(streaming=False)
    with pytest.raises(NotImplementedError, match="disabled via tts.inworld.streaming"):
        providers.InworldTTSProvider().stream("hi", format="opus")


def test_streaming_true_is_not_treated_as_disabled(monkeypatch, tts_cfg):
    tts_cfg(streaming=True)
    monkeypatch.setattr(providers, "_client_for", lambda s: _StubClient([_result(b"a")]))
    assert b"".join(providers.InworldTTSProvider().stream("hi")) == b"a"


def test_over_long_text_raises_eagerly(tts_cfg):
    """Validation errors must also surface before iteration begins."""

    tts_cfg(max_text_length=5)
    with pytest.raises(ValueError, match="above the 5-character"):
        providers.InworldTTSProvider().stream("far too long", format="mp3")


# --- provider-level chunk handling -------------------------------------------


def _result(audio: bytes) -> dict:
    return {"result": {"audioContent": base64.b64encode(audio).decode()}}


class _StubClient:
    def __init__(self, messages, capture=None):
        self.messages = messages
        self.capture = capture if capture is not None else {}

    def request_ndjson(self, method, path, payload=None):
        self.capture.update(method=method, path=path, payload=payload)
        yield from self.messages


def test_stream_hits_the_streaming_endpoint(monkeypatch, tts_cfg):
    tts_cfg()
    capture = {}
    monkeypatch.setattr(providers, "_client_for", lambda s: _StubClient([_result(b"a")], capture))

    list(providers.InworldTTSProvider().stream("hi", voice="Ashley", format="mp3"))
    assert capture["path"] == "/tts/v1/voice:stream"
    assert capture["payload"]["voiceId"] == "Ashley"
    assert capture["payload"]["audioConfig"]["audioEncoding"] == "MP3"


@pytest.mark.parametrize(
    ("fmt", "encoding"),
    [
        ("mp3", "MP3"),
        ("wav", "WAV"),
        ("opus", "OGG_OPUS"),
        ("ogg", "OGG_OPUS"),
        ("flac", "FLAC"),
        ("pcm", "PCM"),
        ("linear16", "LINEAR16"),
        ("OPUS", "OGG_OPUS"),
    ],
)
def test_stream_format_mapping(monkeypatch, tts_cfg, fmt, encoding):
    tts_cfg()
    capture = {}
    monkeypatch.setattr(providers, "_client_for", lambda s: _StubClient([_result(b"a")], capture))
    list(providers.InworldTTSProvider().stream("hi", format=fmt))
    assert capture["payload"]["audioConfig"]["audioEncoding"] == encoding


def test_container_formats_are_concatenated_untouched(monkeypatch, tts_cfg):
    """MP3 chunks are container-framed; Inworld's own example just joins them."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([_result(b"aaa"), _result(b"bbb"), _result(b"ccc")]),
    )
    out = b"".join(providers.InworldTTSProvider().stream("hi", format="mp3"))
    assert out == b"aaabbbccc"


def test_pcm_strips_repeated_riff_headers_after_the_first(monkeypatch, tts_cfg):
    """PCM/LINEAR16 repeat a full WAV header per chunk; joining them clicks."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient(
            [_result(RIFF + b"one"), _result(RIFF + b"two"), _result(RIFF + b"three")]
        ),
    )
    out = b"".join(providers.InworldTTSProvider().stream("hi", format="pcm"))
    assert out == RIFF + b"one" + b"two" + b"three"


def test_wav_keeps_its_single_leading_header(monkeypatch, tts_cfg):
    """WAV carries the header on the first chunk only - leave it alone."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([_result(RIFF + b"one"), _result(b"two")]),
    )
    out = b"".join(providers.InworldTTSProvider().stream("hi", format="wav"))
    assert out == RIFF + b"one" + b"two"


def test_unexpected_repeated_header_is_stripped_and_warned(monkeypatch, tts_cfg, caplog):
    """Defensive path: an encoding we expect to be container-framed misbehaves."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([_result(b"first"), _result(RIFF + b"second")]),
    )
    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        out = b"".join(providers.InworldTTSProvider().stream("hi", format="mp3"))

    assert out == b"first" + b"second"
    assert "repeated a RIFF header" in caplog.text


def test_short_riff_like_chunk_is_not_truncated(monkeypatch, tts_cfg):
    """A chunk shorter than a header must survive intact."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([_result(b"first"), _result(b"RIFFtiny")]),
    )
    out = b"".join(providers.InworldTTSProvider().stream("hi", format="pcm"))
    assert out == b"first" + b"RIFFtiny"


def test_messages_without_audio_are_ignored(monkeypatch, tts_cfg):
    """usage-only and timestamp-only messages carry no audioContent."""

    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient(
            [
                {"result": {"usage": {"processedCharactersCount": 2}}},
                _result(b"audio"),
                {"result": {"timestampInfo": {"words": []}}},
                {"nothing": True},
            ]
        ),
    )
    assert b"".join(providers.InworldTTSProvider().stream("hi", format="mp3")) == b"audio"


def test_stream_with_no_audio_raises(monkeypatch, tts_cfg):
    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([{"result": {"usage": {}}}]),
    )
    with pytest.raises(RuntimeError, match="produced no audio"):
        list(providers.InworldTTSProvider().stream("hi", format="mp3"))


def test_invalid_base64_in_stream_raises(monkeypatch, tts_cfg):
    tts_cfg()
    monkeypatch.setattr(
        providers,
        "_client_for",
        lambda s: _StubClient([{"result": {"audioContent": "not!base64!"}}]),
    )
    with pytest.raises(RuntimeError, match="invalid base64"):
        list(providers.InworldTTSProvider().stream("hi", format="mp3"))


# --- payload parity ----------------------------------------------------------


def test_stream_and_synthesize_build_identical_payloads(monkeypatch, tmp_path, tts_cfg):
    """The two paths must not drift on voice, model, or generation options."""

    tts_cfg(
        voice="Ashley",
        model="inworld-tts-2",
        temperature=1.2,
        instruction="Speak warmly.",
        language="en-GB",
        delivery_mode="creative",
        speaking_rate=0.9,
    )

    stream_capture = {}
    monkeypatch.setattr(
        providers, "_client_for", lambda s: _StubClient([_result(b"a")], stream_capture)
    )
    list(providers.InworldTTSProvider().stream("hello", format="mp3"))

    batch_capture = {}

    class _BatchClient:
        def request_json(self, method, path, payload=None):
            batch_capture.update(payload=payload)
            return {"audioContent": base64.b64encode(b"a").decode()}

    monkeypatch.setattr(providers, "_client_for", lambda s: _BatchClient())
    providers.InworldTTSProvider().synthesize("hello", str(tmp_path / "o.mp3"), format="mp3")

    assert stream_capture["payload"] == batch_capture["payload"]


# --- sample rate validation --------------------------------------------------


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 32000, 44100, 48000])
def test_supported_sample_rates_pass_through(rate):
    assert providers._sample_rate({"sample_rate_hertz": rate}) == rate


@pytest.mark.parametrize("rate", [11025, 96000, 23999, 1])
def test_unsupported_sample_rate_snaps_to_default(rate, caplog):
    """Inworld rejects these server-side; do not spend a request finding out."""

    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        assert providers._sample_rate({"sample_rate_hertz": rate}) == (
            providers.DEFAULT_SAMPLE_RATE_HZ
        )
    assert "sample_rate_hertz" in caplog.text
