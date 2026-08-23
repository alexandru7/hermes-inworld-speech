from __future__ import annotations

import json
import sys
import types
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from hermes_inworld_speech import __version__
from hermes_inworld_speech import client as inworld_client


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _install_hermes_config(monkeypatch, value):
    """Pretend Hermes' config module is importable and returns ``value``."""

    pkg = types.ModuleType("hermes_cli")
    pkg.__path__ = []
    cfg = types.ModuleType("hermes_cli.config")
    cfg.get_env_value = lambda _name: value
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", cfg)


# --- credential resolution ---------------------------------------------------


def test_get_api_key_strips_basic_prefix(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "Basic abc123")
    assert inworld_client.get_api_key() == "abc123"


def test_get_api_key_prefers_hermes_profile(monkeypatch):
    _install_hermes_config(monkeypatch, "from-hermes")
    monkeypatch.setenv("INWORLD_API_KEY", "from-env")
    assert inworld_client.get_api_key() == "from-hermes"


@pytest.mark.parametrize("hermes_value", [None, "", "   "])
def test_get_api_key_falls_back_to_env_when_hermes_has_nothing(monkeypatch, hermes_value):
    """Regression: the env fallback must run even when Hermes' config imports.

    Container deployments inject the credential straight into the process
    environment and never write a profile .env, so an empty Hermes result has
    to fall through instead of short-circuiting to ''.
    """

    _install_hermes_config(monkeypatch, hermes_value)
    monkeypatch.setenv("INWORLD_API_KEY", "from-docker-env")
    assert inworld_client.get_api_key() == "from-docker-env"


def test_get_api_key_returns_empty_when_unset(monkeypatch):
    assert inworld_client.get_api_key() == ""


def test_request_json_requires_a_key(monkeypatch):
    with pytest.raises(RuntimeError, match="INWORLD_API_KEY is not configured"):
        inworld_client.InworldClient().request_json("GET", "/example")


# --- base URL handling -------------------------------------------------------


def test_api_root_defaults_to_inworld(monkeypatch):
    assert inworld_client.api_root() == "https://api.inworld.ai"


def test_api_root_allows_https_override(monkeypatch):
    monkeypatch.setenv("INWORLD_API_BASE_URL", "https://proxy.example.com/")
    assert inworld_client.api_root() == "https://proxy.example.com"


def test_api_root_allows_loopback_http(monkeypatch):
    monkeypatch.setenv("INWORLD_API_BASE_URL", "http://127.0.0.1:8080")
    assert inworld_client.api_root() == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    "override",
    ["http://evil.example.com", "ftp://example.com", "api.inworld.ai"],
)
def test_api_root_rejects_credential_leaking_overrides(monkeypatch, override):
    monkeypatch.setenv("INWORLD_API_BASE_URL", override)
    with pytest.raises(RuntimeError, match="must use https"):
        inworld_client.api_root()


# --- requests and retries ----------------------------------------------------


def test_request_json_sends_basic_auth(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")
    seen = {}

    def fake_urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _Response({"ok": True})

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    result = inworld_client.InworldClient(timeout_seconds=7, max_attempts=1).request_json(
        "POST", "/example", {"x": 1}
    )
    assert result == {"ok": True}
    assert seen["auth"] == "Basic abc123"
    assert seen["timeout"] == 7
    assert seen["agent"] == f"hermes-inworld-speech/{__version__}"


def test_non_retryable_http_error_is_bounded(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")
    calls = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise HTTPError(
            "https://api.inworld.ai/example",
            400,
            "Bad Request",
            {},
            BytesIO(b"invalid request"),
        )

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match=r"Inworld HTTP 400: invalid request"):
        inworld_client.InworldClient(max_attempts=3).request_json("GET", "/example")
    assert len(calls) == 1, "a 400 must not be retried"


def test_error_body_is_truncated(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")

    def fake_urlopen(*_args, **_kwargs):
        raise HTTPError(
            "https://api.inworld.ai/example",
            400,
            "Bad Request",
            {},
            BytesIO(b"x" * 9000),
        )

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        inworld_client.InworldClient(max_attempts=1).request_json("GET", "/example")
    assert len(str(excinfo.value)) < 4200
    assert str(excinfo.value).endswith("…")


def test_retryable_status_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")
    calls = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError(
                "https://api.inworld.ai/example",
                429,
                "Too Many Requests",
                {"Retry-After": "1"},
                BytesIO(b"slow down"),
            )
        return _Response({"ok": True})

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    result = inworld_client.InworldClient(max_attempts=3).request_json("GET", "/example")
    assert result == {"ok": True}
    assert len(calls) == 3


def test_connection_error_exhausts_attempts(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")
    calls = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise URLError("no route to host")

    monkeypatch.setattr(inworld_client, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Inworld connection error"):
        inworld_client.InworldClient(max_attempts=3).request_json("GET", "/example")
    assert len(calls) == 3


def test_empty_response_body_is_an_empty_dict(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")

    class _Empty(_Response):
        def read(self):
            return b""

    monkeypatch.setattr(inworld_client, "urlopen", lambda *a, **k: _Empty(None))
    assert inworld_client.InworldClient(max_attempts=1).request_json("GET", "/example") == {}


def test_non_dict_json_is_rejected(monkeypatch):
    monkeypatch.setenv("INWORLD_API_KEY", "abc123")
    monkeypatch.setattr(inworld_client, "urlopen", lambda *a, **k: _Response([1, 2, 3]))
    with pytest.raises(RuntimeError, match="unexpected JSON response shape"):
        inworld_client.InworldClient(max_attempts=1).request_json("GET", "/example")


@pytest.mark.parametrize(
    ("attempt", "retry_after", "expected"),
    [
        (0, None, 0.5),
        (1, None, 1.0),
        (5, None, 4.0),
        (0, "2", 2.0),
        (0, "9999", 10.0),
        (0, "not-a-number", 0.5),
    ],
)
def test_retry_delay(attempt, retry_after, expected):
    assert inworld_client._retry_delay(attempt, retry_after) == expected
