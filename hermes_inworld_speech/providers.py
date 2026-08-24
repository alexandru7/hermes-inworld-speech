from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    from agent.transcription_provider import TranscriptionProvider
    from agent.tts_provider import TTSProvider
except ImportError as exc:  # pragma: no cover - depends on the host Hermes build
    raise ImportError(
        "hermes-inworld-speech requires a Hermes Agent build that provides "
        "'agent.tts_provider.TTSProvider' and "
        "'agent.transcription_provider.TranscriptionProvider'. Upgrade Hermes to a "
        "build that includes the TTS/STT provider plugin hooks."
    ) from exc

from .client import DEFAULT_MAX_ATTEMPTS, DEFAULT_TIMEOUT_SECONDS, InworldClient, get_api_key

logger = logging.getLogger(__name__)

DEFAULT_TTS_MODEL = "inworld-tts-2-flash"
DEFAULT_TTS_VOICE = "Dennis"
DEFAULT_STT_MODEL = "inworld/inworld-stt-1"
DEFAULT_SAMPLE_RATE_HZ = 24000

# Inworld's documented per-request text ceiling for the TTS-2 family.  A user
# may configure a smaller value; a larger one is clamped back to this.
MODEL_MAX_TEXT_LENGTH = 2000

# Inworld documents speakingRate in audioConfig with this range (default 1.0).
MIN_SPEAKING_RATE = 0.5
MAX_SPEAKING_RATE = 1.5

# Whole-file STT uploads are base64-encoded into a JSON body, costing roughly
# 3x the file size in memory.  Cap it so a stray recording cannot exhaust the
# Hermes process.
DEFAULT_MAX_STT_BYTES = 25 * 1024 * 1024

# format -> (Inworld audioEncoding, file suffix).  ``wav`` maps to the explicit
# WAV encoding rather than LINEAR16: Inworld exposes both, and LINEAR16 in this
# API family is raw headerless PCM, which most players reject in a .wav file.
_FORMAT_MAP: dict[str, tuple[str, str]] = {
    "mp3": ("MP3", ".mp3"),
    "wav": ("WAV", ".wav"),
    "ogg": ("OGG_OPUS", ".ogg"),
    "opus": ("OGG_OPUS", ".ogg"),
    "flac": ("FLAC", ".flac"),
}

_AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".opus", ".flac"}

# Sample rates Inworld accepts.  Anything else is rejected server-side, so snap
# to the default rather than sending a request that is certain to 400.
SUPPORTED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)

# Formats the streaming endpoint can produce, mapped to Hermes' format names.
# Every value here is also a valid batch encoding.
_STREAM_FORMATS: dict[str, str] = {
    "mp3": "MP3",
    "wav": "WAV",
    "ogg": "OGG_OPUS",
    "opus": "OGG_OPUS",
    "flac": "FLAC",
    "pcm": "PCM",
    "linear16": "LINEAR16",
}

# Encodings that may repeat a full RIFF/WAV header on *every* streamed chunk so
# each chunk plays standalone.  Concatenating those unmodified yields an audible
# click at every boundary, so the header is stripped from all but the first.
#
# Observed against the live API on 2026-08-24 (scripts/verify_streaming.py):
#
#   PCM       raw headerless samples, no RIFF header anywhere
#   LINEAR16  RIFF header on the first chunk only
#   WAV       RIFF header on the first chunk only
#   OGG_OPUS  proper page-based stream; every chunk starts with an OggS page
#             marker, which is how Ogg works - it is NOT a repeated file header
#             and must not be stripped
#   MP3/FLAC  container-framed, concatenate directly
#
# That contradicts Inworld's API reference, which claims PCM and LINEAR16 carry
# a header on every chunk.  Both entries are kept as belt-and-braces: the check
# in _strip_riff_header() verifies the RIFF/WAVE magic before removing anything,
# so on the observed behaviour it is a no-op, and it still protects if a model
# or sample-rate combination ever does repeat headers.
_PER_CHUNK_RIFF_ENCODINGS = {"PCM", "LINEAR16"}

_RIFF_HEADER_BYTES = 44


def _sample_rate(cfg: dict[str, Any]) -> int:
    rate = _positive_int(
        cfg.get("sample_rate_hertz"), DEFAULT_SAMPLE_RATE_HZ, "tts.inworld.sample_rate_hertz"
    )
    if rate not in SUPPORTED_SAMPLE_RATES:
        logger.warning(
            "tts.inworld.sample_rate_hertz=%s is not supported by Inworld (%s); using %s.",
            rate,
            ", ".join(str(r) for r in SUPPORTED_SAMPLE_RATES),
            DEFAULT_SAMPLE_RATE_HZ,
        )
        return DEFAULT_SAMPLE_RATE_HZ
    return rate


def _strip_riff_header(chunk: bytes) -> bytes:
    """Drop a leading 44-byte RIFF/WAVE header if this chunk carries one."""

    if len(chunk) > _RIFF_HEADER_BYTES and chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE":
        return chunk[_RIFF_HEADER_BYTES:]
    return chunk


def _provider_config(section: str) -> dict[str, Any]:
    """Read ``<section>.inworld`` from Hermes config.

    Hermes does not yet expose third-party provider fields in every config UI,
    so keeping these settings in the established ``tts.<provider>`` and
    ``stt.<provider>`` namespaces makes the plugin usable today while remaining
    compatible with the provider dispatchers.
    """

    try:
        from hermes_cli.config import load_config

        root = load_config().get(section) or {}
        value = root.get("inworld") if isinstance(root, dict) else None
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        logger.debug(
            "Could not read %s.inworld from Hermes config (%s); using defaults.", section, exc
        )
        return {}


def _positive_float(value: Any, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric %s=%r; using %s.", name, value, default)
        return default
    if result <= 0:
        logger.warning("Ignoring non-positive %s=%r; using %s.", name, value, default)
        return default
    return result


def _positive_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-integer %s=%r; using %s.", name, value, default)
        return default
    if result <= 0:
        logger.warning("Ignoring non-positive %s=%r; using %s.", name, value, default)
        return default
    return result


def _client_for(section: str) -> InworldClient:
    cfg = _provider_config(section)
    timeout = _positive_float(
        cfg.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, f"{section}.inworld.timeout_seconds"
    )
    attempts = _positive_int(
        cfg.get("max_attempts"), DEFAULT_MAX_ATTEMPTS, f"{section}.inworld.max_attempts"
    )
    return InworldClient(timeout_seconds=timeout, max_attempts=attempts)


def _max_text_length(cfg: dict[str, Any]) -> int:
    limit = _positive_int(
        cfg.get("max_text_length"), MODEL_MAX_TEXT_LENGTH, "tts.inworld.max_text_length"
    )
    if limit > MODEL_MAX_TEXT_LENGTH:
        logger.warning(
            "tts.inworld.max_text_length=%s exceeds the Inworld per-request limit; using %s.",
            limit,
            MODEL_MAX_TEXT_LENGTH,
        )
        return MODEL_MAX_TEXT_LENGTH
    return limit


def _normalize_voice_language(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "_" in text:
        parts = text.split("_")
        if len(parts) == 2:
            return f"{parts[0].lower()}-{parts[1].upper()}"
    return text


def _tts_encoding_and_path(fmt: str, output_path: str) -> tuple[str, Path]:
    requested = (fmt or "mp3").strip().lower()
    if requested not in _FORMAT_MAP:
        logger.warning("Unsupported tts.output_format=%r; falling back to mp3.", fmt)
        requested = "mp3"
    encoding, suffix = _FORMAT_MAP[requested]

    target = Path(output_path)
    # Only replace a suffix that is already an audio extension.  ``with_suffix``
    # on a dotted name such as ``reply_2026.08.23`` would silently truncate it
    # to ``reply_2026.08.mp3`` and collide with other requests in that month.
    if target.suffix.lower() in _AUDIO_SUFFIXES:
        target = target.with_suffix(suffix)
    elif target.suffix.lower() != suffix:
        target = target.with_name(target.name + suffix)

    if str(target) != output_path:
        logger.debug("TTS output path %s -> %s (format %s).", output_path, target, requested)
    return encoding, target


def _speaking_rate(raw: Any) -> float | None:
    """Clamp a requested speaking rate into Inworld's supported range."""

    if raw is None:
        return None
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric speaking rate %r.", raw)
        return None

    clamped = min(max(rate, MIN_SPEAKING_RATE), MAX_SPEAKING_RATE)
    if clamped != rate:
        logger.warning(
            "Speaking rate %s is outside Inworld's supported range [%s, %s]; using %s.",
            rate,
            MIN_SPEAKING_RATE,
            MAX_SPEAKING_RATE,
            clamped,
        )
    return clamped


class InworldTTSProvider(TTSProvider):
    @property
    def name(self) -> str:
        return "inworld"

    @property
    def display_name(self) -> str:
        return "Inworld TTS"

    def is_available(self) -> bool:
        return bool(get_api_key())

    def list_models(self) -> list[dict[str, Any]]:
        limit = _max_text_length(_provider_config("tts"))
        return [
            {
                "id": "inworld-tts-2-flash",
                "display": "Inworld TTS-2 Flash",
                "max_text_length": limit,
            },
            {
                "id": "inworld-tts-2",
                "display": "Inworld TTS-2",
                "max_text_length": limit,
            },
        ]

    def list_voices(self) -> list[dict[str, Any]]:
        try:
            result = InworldClient(timeout_seconds=15.0, max_attempts=2).request_json(
                "GET", "/voices/v1/voices"
            )
            voices: list[dict[str, Any]] = []
            for item in result.get("voices", []):
                if not isinstance(item, dict):
                    continue
                voice_id = item.get("voiceId")
                if not voice_id:
                    continue
                voices.append(
                    {
                        "id": str(voice_id),
                        "display": str(item.get("displayName") or voice_id),
                        "language": _normalize_voice_language(item.get("langCode")),
                    }
                )
            if voices:
                return voices
            logger.warning("Inworld voice catalog returned no usable entries; using fallback list.")
        except Exception as exc:
            # Voice discovery is optional; setup should still work if the catalog
            # endpoint is temporarily unavailable.
            logger.warning("Inworld voice discovery failed (%s); using fallback list.", exc)

        return [
            {"id": "Dennis", "display": "Dennis", "language": "en-US"},
            {"id": "Ashley", "display": "Ashley", "language": "en-US"},
        ]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Inworld TTS",
            "badge": "paid",
            "tag": "TTS-2 / TTS-2 Flash",
            "env_vars": [
                {
                    "key": "INWORLD_API_KEY",
                    "prompt": "Inworld Base64 API credential",
                    "url": "https://platform.inworld.ai/",
                }
            ],
        }

    @property
    def voice_compatible(self) -> bool:
        return True

    def _build_payload(
        self,
        text: str,
        encoding: str,
        cfg: dict[str, Any],
        *,
        voice: str | None,
        model: str | None,
        speed: float | None,
    ) -> dict[str, Any]:
        """Build the request body shared by synthesize() and stream().

        Both paths must agree on voice, model, and every generation option;
        keeping one builder is what stops them drifting.
        """

        selected_voice = voice or cfg.get("voice") or DEFAULT_TTS_VOICE
        selected_model = model or cfg.get("model") or DEFAULT_TTS_MODEL

        limit = _max_text_length(cfg)
        if len(text) > limit:
            raise ValueError(
                f"Text is {len(text)} characters, above the {limit}-character Inworld "
                "per-request limit. Hermes should split longer text into chunks; see "
                "tts.inworld.max_text_length."
            )

        audio_config: dict[str, Any] = {
            "audioEncoding": encoding,
            "sampleRateHertz": _sample_rate(cfg),
        }

        # Hermes passes a generic speed argument; Inworld exposes it as
        # audioConfig.speakingRate. Config provides the default when Hermes
        # does not supply one.
        rate = _speaking_rate(speed if speed is not None else cfg.get("speaking_rate"))
        if rate is not None:
            audio_config["speakingRate"] = rate

        bit_rate = cfg.get("bit_rate")
        if bit_rate is not None:
            resolved = _positive_int(bit_rate, 0, "tts.inworld.bit_rate")
            if resolved:
                audio_config["bitRate"] = resolved

        payload: dict[str, Any] = {
            "text": text,
            "voiceId": str(selected_voice),
            "modelId": str(selected_model),
            "audioConfig": audio_config,
        }

        delivery_mode = cfg.get("delivery_mode")
        if isinstance(delivery_mode, str) and delivery_mode.strip():
            payload["deliveryMode"] = delivery_mode.strip().upper()

        language = cfg.get("language")
        if isinstance(language, str) and language.strip() and language.strip().lower() != "auto":
            payload["language"] = language.strip()

        instruction = cfg.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            payload["instruction"] = instruction.strip()

        temperature = cfg.get("temperature")
        if temperature is not None:
            try:
                value = float(temperature)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring non-numeric tts.inworld.temperature=%r; expected a number in (0, 2].",
                    temperature,
                )
            else:
                if 0 < value <= 2:
                    payload["temperature"] = value
                else:
                    logger.warning(
                        "Ignoring tts.inworld.temperature=%r; expected a number in (0, 2].",
                        temperature,
                    )

        normalize = cfg.get("apply_text_normalization")
        if isinstance(normalize, str) and normalize.strip():
            payload["applyTextNormalization"] = normalize.strip().upper()

        enhance = cfg.get("enhance_generation")
        if isinstance(enhance, bool):
            payload["enhanceGeneration"] = enhance

        logger.debug(
            "Inworld TTS: model=%s voice=%s encoding=%s chars=%d",
            selected_model,
            selected_voice,
            encoding,
            len(text),
        )
        return payload

    def stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        format: str = "opus",
        **extra: Any,
    ) -> Iterator[bytes]:
        """Yield audio chunks as Inworld produces them.

        Deliberately *not* a generator function.  The base-class contract is
        that an unsupported provider raises NotImplementedError when ``stream``
        is **called**, which is Hermes' signal to fall back to synthesize().  A
        generator body would defer that raise until the first iteration, by
        which point the dispatcher has already committed to the streaming path.
        So validate eagerly here and return a separate generator.
        """

        cfg = _provider_config("tts")

        if cfg.get("streaming") is False:
            raise NotImplementedError(
                "Inworld streaming synthesis is disabled via tts.inworld.streaming."
            )

        requested = (format or "opus").strip().lower()
        encoding = _STREAM_FORMATS.get(requested)
        if encoding is None:
            raise NotImplementedError(
                f"Inworld streaming synthesis does not support format {requested!r}. "
                f"Supported: {', '.join(sorted(_STREAM_FORMATS))}."
            )

        # The base signature omits `speed` even though its docstring says the
        # args mirror synthesize(), so accept it from **extra if Hermes sends it.
        payload = self._build_payload(
            text, encoding, cfg, voice=voice, model=model, speed=extra.get("speed")
        )
        return self._iter_audio_chunks(payload, encoding)

    def _iter_audio_chunks(self, payload: dict[str, Any], encoding: str) -> Iterator[bytes]:
        strip_every_chunk = encoding in _PER_CHUNK_RIFF_ENCODINGS
        index = 0

        for message in _client_for("tts").request_ndjson("POST", "/tts/v1/voice:stream", payload):
            result = message.get("result")
            if not isinstance(result, dict):
                continue
            encoded = result.get("audioContent")
            if not isinstance(encoded, str) or not encoded:
                continue

            try:
                chunk = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise RuntimeError("Inworld TTS stream returned invalid base64 audio.") from exc

            if index and (strip_every_chunk or chunk[:4] == b"RIFF"):
                stripped = _strip_riff_header(chunk)
                if stripped is not chunk and not strip_every_chunk:
                    # Only reachable if an encoding we expected to be
                    # container-framed repeats headers instead; worth knowing.
                    logger.warning(
                        "Inworld %s stream repeated a RIFF header on chunk %d; "
                        "stripping it. Please report this against the plugin.",
                        encoding,
                        index,
                    )
                chunk = stripped

            if chunk:
                index += 1
                yield chunk

        if not index:
            raise RuntimeError("Inworld TTS stream produced no audio.")
        logger.debug("Inworld TTS stream complete: %d chunk(s), encoding=%s.", index, encoding)

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        speed: float | None = None,
        format: str = "mp3",
        **extra: Any,
    ) -> str:
        cfg = _provider_config("tts")
        encoding, target = _tts_encoding_and_path(format, output_path)
        payload = self._build_payload(text, encoding, cfg, voice=voice, model=model, speed=speed)

        result = _client_for("tts").request_json("POST", "/tts/v1/voice", payload)
        audio_content = result.get("audioContent")
        if not isinstance(audio_content, str) or not audio_content:
            raise RuntimeError("Inworld TTS returned no audioContent.")

        try:
            audio = base64.b64decode(audio_content, validate=True)
        except Exception as exc:
            raise RuntimeError("Inworld TTS returned invalid base64 audio data.") from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(audio)
        return str(target)


class InworldSTTProvider(TranscriptionProvider):
    @property
    def name(self) -> str:
        return "inworld"

    @property
    def display_name(self) -> str:
        return "Inworld STT"

    def is_available(self) -> bool:
        return bool(get_api_key())

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": DEFAULT_STT_MODEL, "display": "Inworld STT-1"}]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Inworld STT",
            "badge": "paid",
            "tag": "Inworld STT-1",
            "env_vars": [
                {
                    "key": "INWORLD_API_KEY",
                    "prompt": "Inworld Base64 API credential",
                    "url": "https://platform.inworld.ai/",
                }
            ],
        }

    def transcribe(
        self,
        file_path: str,
        *,
        model: str | None = None,
        language: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        try:
            cfg = _provider_config("stt")
            selected_model = model or cfg.get("model") or DEFAULT_STT_MODEL
            selected_language = language if language is not None else cfg.get("language")

            if isinstance(selected_language, str):
                selected_language = selected_language.strip()
            if not selected_language or str(selected_language).lower() == "auto":
                selected_language = None

            source = Path(file_path)
            if not source.is_file():
                raise FileNotFoundError(f"Audio file not found: {source}")

            max_bytes = _positive_int(
                cfg.get("max_file_bytes"), DEFAULT_MAX_STT_BYTES, "stt.inworld.max_file_bytes"
            )
            size = source.stat().st_size
            if size > max_bytes:
                raise ValueError(
                    f"Audio file is {size} bytes, above the {max_bytes}-byte limit for a "
                    "single Inworld STT request. Split the recording or raise "
                    "stt.inworld.max_file_bytes."
                )

            audio_b64 = base64.b64encode(source.read_bytes()).decode("ascii")
            transcribe_config: dict[str, Any] = {
                "modelId": str(selected_model),
                "audioEncoding": "AUTO_DETECT",
            }
            if selected_language:
                transcribe_config["language"] = str(selected_language)

            logger.debug(
                "Inworld STT: model=%s language=%s bytes=%d",
                selected_model,
                selected_language or "auto",
                size,
            )

            result = _client_for("stt").request_json(
                "POST",
                "/stt/v1/transcribe",
                {
                    "transcribeConfig": transcribe_config,
                    "audioData": {"content": audio_b64},
                },
            )

            transcription = result.get("transcription") or {}
            transcript = str(transcription.get("transcript") or "").strip()
            if not transcript:
                raise RuntimeError("Inworld STT returned no transcript.")

            return {
                "success": True,
                "transcript": transcript,
                "provider": self.name,
            }
        except Exception as exc:
            logger.warning("Inworld STT failed for %s: %s", file_path, exc)
            return {
                "success": False,
                "transcript": "",
                "error": str(exc),
                "provider": self.name,
            }
