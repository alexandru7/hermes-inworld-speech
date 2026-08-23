from __future__ import annotations

import re
from pathlib import Path

from hermes_inworld_speech import __version__

ROOT = Path(__file__).resolve().parents[1]


def _manifest_version() -> str:
    for line in (ROOT / "plugin.yaml").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    raise AssertionError("plugin.yaml has no version key")


def test_manifest_and_package_versions_match():
    """The manifest and the package are versioned separately; keep them in step."""

    assert _manifest_version() == __version__


def test_changelog_documents_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(rf"^## {re.escape(__version__)}\b", changelog, re.MULTILINE)


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
