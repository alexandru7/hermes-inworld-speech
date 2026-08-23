"""Inworld TTS and STT provider plugin for Hermes Agent."""

from __future__ import annotations

import sys
from pathlib import Path

# Hermes loads user plugins from their directory.  Put the plugin root on
# sys.path so the private package below imports reliably across loader
# implementations.  Append rather than insert: prepending would shadow stdlib
# and Hermes modules with this directory's contents (tests/, docs/, examples/)
# for every import in the host process.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.append(_PLUGIN_ROOT)

# E402: these must follow the sys.path setup above, which is the whole point of
# this module — the package is not importable until the plugin root is on the path.
from hermes_inworld_speech import __version__  # noqa: E402
from hermes_inworld_speech.providers import (  # noqa: E402
    InworldSTTProvider,
    InworldTTSProvider,
)

__all__ = ["InworldTTSProvider", "InworldSTTProvider", "register", "__version__"]


def register(ctx) -> None:
    missing = [
        name
        for name in ("register_tts_provider", "register_transcription_provider")
        if not hasattr(ctx, name)
    ]
    if missing:
        raise RuntimeError(
            "This plugin requires a Hermes Agent build with TTS/STT provider "
            f"plugin hooks. Missing PluginContext methods: {', '.join(missing)}"
        )

    ctx.register_tts_provider(InworldTTSProvider())
    ctx.register_transcription_provider(InworldSTTProvider())
