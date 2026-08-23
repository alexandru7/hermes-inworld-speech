# Contributing

Contributions are welcome.

## Development setup

This plugin intentionally has no runtime dependencies beyond Hermes and Python's standard library. Please keep it that way — it is installed into other people's containers.

```bash
python -m pip install pytest ruff
python -m pytest
ruff check .
ruff format .
```

Both lint and tests run in CI across Python 3.10–3.13.

The tests stub Hermes' provider base classes (see `tests/conftest.py`), so the suite runs without a Hermes install and without an Inworld account. All HTTP is mocked; no test makes a network call.

## Pull requests

Please include:

- a focused description of the change
- tests for request-shape or error-handling changes
- README/docs updates for new user-facing configuration
- no API keys, credentials, or captured private audio

## Conventions

- **Invalid config is logged and ignored**, falling back to the documented default, rather than raising. The exception is input that would produce silently wrong output — text over `max_text_length`, for example, raises rather than being truncated.
- **Log through the module logger**, never `print`. Never log the API key.
- **New config keys need four things**: the read site, a default, a row in the README table, and a commented line in `examples/config.yaml`.
- **Verify API fields against Inworld's reference** before adding them to a payload. Sending an unknown field risks a 400 for every user.

## Compatibility

Avoid importing private Hermes internals when a public provider ABC or `PluginContext` method exists. The goal is to keep this plugin out-of-tree and upgrade-friendly.

The plugin fails loudly on incompatible Hermes builds in two places: the `agent.*` import in `hermes_inworld_speech/providers.py`, and the hook check in `register()`. Keep both — the import fires first and the registration check covers builds where the modules exist but the `PluginContext` hooks do not.
