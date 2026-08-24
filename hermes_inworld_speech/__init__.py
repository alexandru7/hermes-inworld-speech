"""Inworld speech provider implementation for Hermes Agent.

The version declared here is the single source of truth for the Python side of
the plugin.  ``plugin.yaml`` carries its own copy for the Hermes plugin loader;
``tests/test_version.py`` asserts the two stay in step.
"""

from __future__ import annotations

__version__ = "1.1.0"

__all__ = ["__version__"]
