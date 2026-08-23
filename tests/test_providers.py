from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest

from hermes_inworld_speech import providers


@pytest.fixture
def no_config(monkeypatch):
    monkeypatch.setattr(providers, "_provider_config", lambda section: {})


@pytest.fixture
def tts_config(monkeypatch):
    def _apply(**values):
        monkeypatch.setattr(
            providers,
            "_provider_config",
            lambda section: dict(values) if section == "tts" else {},
        )

    return _apply


def _fake_client(monkeypatch, captured, response):
    class FakeClient:
        def request_json(self, method, path, payload=None):
            captured.update(method=method, path=path, payload=payload)
            return response

    monkeypatch.setattr(providers, "_client_for", lambda section: FakeClient())


# --- TTS payload and output paths -------------------------------------------


def test_tts_mp3_payload_and_file(monkeypatch, tmp_path, no_config):
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"mp3-bytes").decode()})

    path = providers.InworldTTSProvider().synthesize(
        "hello",
        str(tmp_path / "speech"),
        voice="Dennis",
        model="inworld-tts-2-flash",
        format="mp3",
    )

    assert Path(path).suffix == ".mp3"
    assert Path(path).read_bytes() == b"mp3-bytes"
    assert captured["method"] == "POST"
    assert captured["path"] == "/tts/v1/voice"
    assert captured["payload"]["voiceId"] == "Dennis"
    assert captured["payload"]["modelId"] == "inworld-tts-2-flash"
    assert captured["payload"]["audioConfig"]["audioEncoding"] == "MP3"
    assert captured["payload"]["audioConfig"]["sampleRateHertz"] == 24000


@pytest.mark.parametrize(
    ("fmt", "encoding", "suffix"),
    [
        ("mp3", "MP3", ".mp3"),
        ("wav", "WAV", ".wav"),
        ("ogg", "OGG_OPUS", ".ogg"),
        ("opus", "OGG_OPUS", ".ogg"),
        ("flac", "FLAC", ".flac"),
        ("FLAC", "FLAC", ".flac"),
        ("  mp3  ", "MP3", ".mp3"),
        ("aiff", "MP3", ".mp3"),
        ("", "MP3", ".mp3"),
    ],
)
def test_format_mapping(fmt, encoding, suffix):
    got_encoding, target = providers._tts_encoding_and_path(fmt, "/tmp/out/speech")
    assert got_encoding == encoding
    assert target == Path(f"/tmp/out/speech{suffix}")


def test_wav_uses_container_encoding_not_raw_pcm():
    """LINEAR16 is headerless PCM; a .wav file needs the WAV encoding."""

    encoding, _ = providers._tts_encoding_and_path("wav", "/tmp/out/speech")
    assert encoding == "WAV"


@pytest.mark.parametrize(
    ("output_path", "expected"),
    [
        # Regression: with_suffix() would truncate this to reply_2026.08.mp3.
        ("/tmp/reply_2026.08.23_turn", "/tmp/reply_2026.08.23_turn.mp3"),
        ("/tmp/a.b.c_d", "/tmp/a.b.c_d.mp3"),
        # An existing audio suffix is replaced, not stacked.
        ("/tmp/speech.wav", "/tmp/speech.mp3"),
        ("/tmp/speech.mp3", "/tmp/speech.mp3"),
        # No suffix at all.
        ("/tmp/speech", "/tmp/speech.mp3"),
    ],
)
def test_output_path_never_truncates_dotted_names(output_path, expected):
    _, target = providers._tts_encoding_and_path("mp3", output_path)
    assert target == Path(expected)


def test_tts_creates_missing_parent_directory(monkeypatch, tmp_path, no_config):
    _fake_client(monkeypatch, {}, {"audioContent": base64.b64encode(b"a").decode()})
    nested = tmp_path / "deep" / "nested" / "speech.mp3"
    path = providers.InworldTTSProvider().synthesize("hi", str(nested))
    assert Path(path).exists()


# --- TTS options -------------------------------------------------------------


def test_speed_is_sent_as_speaking_rate(monkeypatch, tmp_path, no_config):
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})

    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"), speed=1.25)
    assert captured["payload"]["audioConfig"]["speakingRate"] == 1.25


@pytest.mark.parametrize(("given", "expected"), [(0.1, 0.5), (9.0, 1.5), (1.0, 1.0)])
def test_speaking_rate_is_clamped_to_supported_range(given, expected):
    assert providers._speaking_rate(given) == expected


def test_speaking_rate_omitted_when_not_requested(monkeypatch, tmp_path, no_config):
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})
    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))
    assert "speakingRate" not in captured["payload"]["audioConfig"]


def test_config_supplies_speaking_rate_when_hermes_does_not(monkeypatch, tmp_path, tts_config):
    tts_config(speaking_rate=0.8)
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})
    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))
    assert captured["payload"]["audioConfig"]["speakingRate"] == 0.8


def test_invalid_temperature_is_dropped_with_a_warning(monkeypatch, tmp_path, tts_config, caplog):
    tts_config(temperature=3.0)
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})

    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))

    assert "temperature" not in captured["payload"]
    assert "temperature" in caplog.text


def test_valid_temperature_is_sent(monkeypatch, tmp_path, tts_config):
    tts_config(temperature=1.4)
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})
    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))
    assert captured["payload"]["temperature"] == 1.4


def test_optional_fields_are_forwarded(monkeypatch, tmp_path, tts_config):
    tts_config(
        delivery_mode="balanced",
        language="en-US",
        instruction="Speak warmly.",
        apply_text_normalization="on",
        enhance_generation=True,
        bit_rate=128000,
    )
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})

    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))
    payload = captured["payload"]
    assert payload["deliveryMode"] == "BALANCED"
    assert payload["language"] == "en-US"
    assert payload["instruction"] == "Speak warmly."
    assert payload["applyTextNormalization"] == "ON"
    assert payload["enhanceGeneration"] is True
    assert payload["audioConfig"]["bitRate"] == 128000


def test_language_auto_is_not_sent(monkeypatch, tmp_path, tts_config):
    tts_config(language="auto")
    captured = {}
    _fake_client(monkeypatch, captured, {"audioContent": base64.b64encode(b"a").decode()})
    providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))
    assert "language" not in captured["payload"]


# --- max_text_length ---------------------------------------------------------


def test_text_over_the_limit_is_rejected(monkeypatch, tmp_path, tts_config):
    tts_config(max_text_length=10)
    _fake_client(monkeypatch, {}, {"audioContent": base64.b64encode(b"a").decode()})

    with pytest.raises(ValueError, match="above the 10-character"):
        providers.InworldTTSProvider().synthesize("x" * 11, str(tmp_path / "s.mp3"))


def test_configured_limit_is_reported_to_hermes(monkeypatch, tts_config):
    tts_config(max_text_length=500)
    assert all(m["max_text_length"] == 500 for m in providers.InworldTTSProvider().list_models())


def test_configured_limit_is_clamped_to_the_model_ceiling(monkeypatch, tts_config, caplog):
    tts_config(max_text_length=99999)
    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        models = providers.InworldTTSProvider().list_models()
    assert all(m["max_text_length"] == providers.MODEL_MAX_TEXT_LENGTH for m in models)
    assert "max_text_length" in caplog.text


# --- TTS response handling ---------------------------------------------------


def test_missing_audio_content_raises(monkeypatch, tmp_path, no_config):
    _fake_client(monkeypatch, {}, {"audioContent": ""})
    with pytest.raises(RuntimeError, match="no audioContent"):
        providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))


def test_invalid_base64_raises(monkeypatch, tmp_path, no_config):
    _fake_client(monkeypatch, {}, {"audioContent": "not!valid!base64"})
    with pytest.raises(RuntimeError, match="invalid base64"):
        providers.InworldTTSProvider().synthesize("hi", str(tmp_path / "s.mp3"))


# --- voices ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("en_US", "en-US"), ("EN_us", "en-US"), ("en-GB", "en-GB"), ("", None), (None, None)],
)
def test_normalize_voice_language(raw, expected):
    assert providers._normalize_voice_language(raw) == expected


def test_list_voices_maps_the_catalog(monkeypatch):
    class FakeClient:
        def request_json(self, method, path, payload=None):
            return {
                "voices": [
                    {"voiceId": "Dennis", "displayName": "Dennis", "langCode": "en_US"},
                    {"voiceId": "Custom-1"},
                    {"noVoiceId": True},
                    "not-a-dict",
                ]
            }

    monkeypatch.setattr(providers, "InworldClient", lambda **kwargs: FakeClient())
    voices = providers.InworldTTSProvider().list_voices()
    assert voices == [
        {"id": "Dennis", "display": "Dennis", "language": "en-US"},
        {"id": "Custom-1", "display": "Custom-1", "language": None},
    ]


def test_list_voices_falls_back_when_discovery_fails(monkeypatch, caplog):
    class FakeClient:
        def request_json(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

    monkeypatch.setattr(providers, "InworldClient", lambda **kwargs: FakeClient())
    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        voices = providers.InworldTTSProvider().list_voices()

    assert [v["id"] for v in voices] == ["Dennis", "Ashley"]
    assert "catalog down" in caplog.text


# --- STT ---------------------------------------------------------------------


def test_stt_payload(monkeypatch, tmp_path, no_config):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"wave-data")
    captured = {}
    _fake_client(monkeypatch, captured, {"transcription": {"transcript": "hello world"}})

    result = providers.InworldSTTProvider().transcribe(
        str(audio), model="inworld/inworld-stt-1", language="en-US"
    )

    assert result == {"success": True, "transcript": "hello world", "provider": "inworld"}
    assert captured["path"] == "/stt/v1/transcribe"
    assert captured["payload"]["transcribeConfig"] == {
        "modelId": "inworld/inworld-stt-1",
        "audioEncoding": "AUTO_DETECT",
        "language": "en-US",
    }
    assert base64.b64decode(captured["payload"]["audioData"]["content"]) == b"wave-data"


@pytest.mark.parametrize("language", ["auto", "AUTO", "  ", None])
def test_stt_omits_language_when_auto(monkeypatch, tmp_path, no_config, language):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"data")
    captured = {}
    _fake_client(monkeypatch, captured, {"transcription": {"transcript": "hi"}})

    providers.InworldSTTProvider().transcribe(str(audio), language=language)
    assert "language" not in captured["payload"]["transcribeConfig"]


def test_stt_returns_error_envelope_for_missing_file(no_config):
    result = providers.InworldSTTProvider().transcribe("/definitely/missing.wav")
    assert result["success"] is False
    assert result["provider"] == "inworld"
    assert "Audio file not found" in result["error"]


def test_stt_rejects_oversized_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(
        providers,
        "_provider_config",
        lambda section: {"max_file_bytes": 10} if section == "stt" else {},
    )
    audio = tmp_path / "big.wav"
    audio.write_bytes(b"x" * 64)

    result = providers.InworldSTTProvider().transcribe(str(audio))
    assert result["success"] is False
    assert "above the 10-byte limit" in result["error"]


def test_stt_empty_transcript_is_an_error(monkeypatch, tmp_path, no_config):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"data")
    _fake_client(monkeypatch, {}, {"transcription": {"transcript": "   "}})

    result = providers.InworldSTTProvider().transcribe(str(audio))
    assert result["success"] is False
    assert "no transcript" in result["error"]


# --- config coercion ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 5), ("nonsense", 5), (-1, 5), (0, 5), (12, 12), ("7", 7)],
)
def test_positive_int_coercion(value, expected):
    assert providers._positive_int(value, 5, "example") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 2.5), ("nonsense", 2.5), (-1.0, 2.5), (0.0, 2.5), (9.5, 9.5)],
)
def test_positive_float_coercion(value, expected):
    assert providers._positive_float(value, 2.5, "example") == expected


def test_client_for_uses_configured_timeout_and_attempts(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_provider_config",
        lambda section: {"timeout_seconds": 12, "max_attempts": 5},
    )
    client = providers._client_for("tts")
    assert client.timeout_seconds == 12
    assert client.max_attempts == 5


def test_client_for_falls_back_on_bad_values(monkeypatch, caplog):
    monkeypatch.setattr(
        providers,
        "_provider_config",
        lambda section: {"timeout_seconds": "soon", "max_attempts": 0},
    )
    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        client = providers._client_for("tts")

    assert client.timeout_seconds == providers.DEFAULT_TIMEOUT_SECONDS
    assert client.max_attempts == providers.DEFAULT_MAX_ATTEMPTS
    assert "timeout_seconds" in caplog.text


def test_provider_config_survives_a_broken_hermes_config(monkeypatch):
    """A malformed config must degrade to defaults, not crash the provider."""

    import sys
    import types

    pkg = types.ModuleType("hermes_cli")
    pkg.__path__ = []
    cfg = types.ModuleType("hermes_cli.config")

    def _boom():
        raise ValueError("config.yaml is malformed")

    cfg.load_config = _boom
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", cfg)

    assert providers._provider_config("tts") == {}
