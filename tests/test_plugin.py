from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_root():
    spec = importlib.util.spec_from_file_location(
        "hermes_inworld_plugin_root", ROOT / "__init__.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_plugin_registers_both_providers():
    module = _load_plugin_root()
    seen = []

    class Ctx:
        def register_tts_provider(self, provider):
            seen.append(("tts", provider.name))

        def register_transcription_provider(self, provider):
            seen.append(("stt", provider.name))

    module.register(Ctx())
    assert seen == [("tts", "inworld"), ("stt", "inworld")]


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        ("register_tts_provider", "register_transcription_provider"),
        ("register_transcription_provider", "register_tts_provider"),
    ],
)
def test_register_reports_missing_hermes_hooks(present, missing):
    """An older Hermes build must fail with an actionable message."""

    module = _load_plugin_root()
    ctx = type("Ctx", (), {present: lambda self, provider: None})()

    with pytest.raises(RuntimeError, match=missing):
        module.register(ctx)


def test_plugin_root_does_not_shadow_stdlib_imports():
    """The plugin root is appended to sys.path, never prepended.

    Prepending would let this directory's tests/, docs/, and examples/ shadow
    modules for every import in the host Hermes process.
    """

    import sys

    module = _load_plugin_root()
    assert module  # loaded, so the sys.path mutation has run
    root = str(ROOT)
    entries = [i for i, p in enumerate(sys.path) if p == root]
    assert entries, "plugin root should be on sys.path"
    # conftest inserts it at 0 for the test run; the plugin itself must not.
    source = (ROOT / "__init__.py").read_text()
    assert "sys.path.append(_PLUGIN_ROOT)" in source
    assert "sys.path.insert" not in source
