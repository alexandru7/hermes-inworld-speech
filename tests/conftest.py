from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The provider base classes are imported from Hermes at runtime.  Tests use tiny
# compatible stubs so request-shape logic can be validated outside Hermes.
agent_pkg = sys.modules.setdefault("agent", types.ModuleType("agent"))

if "agent.tts_provider" not in sys.modules:
    mod = types.ModuleType("agent.tts_provider")

    class TTSProvider:
        pass

    mod.TTSProvider = TTSProvider
    sys.modules["agent.tts_provider"] = mod
    agent_pkg.tts_provider = mod

if "agent.transcription_provider" not in sys.modules:
    mod = types.ModuleType("agent.transcription_provider")

    class TranscriptionProvider:
        pass

    mod.TranscriptionProvider = TranscriptionProvider
    sys.modules["agent.transcription_provider"] = mod
    agent_pkg.transcription_provider = mod


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Keep retry tests fast without changing the backoff arithmetic."""

    from hermes_inworld_speech import client as inworld_client

    monkeypatch.setattr(inworld_client.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Stop a developer's real credential or base-URL override leaking in."""

    monkeypatch.delenv("INWORLD_API_KEY", raising=False)
    monkeypatch.delenv("INWORLD_API_BASE_URL", raising=False)
    # Force the process-environment path unless a test opts into the Hermes one.
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)
