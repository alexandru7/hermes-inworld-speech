# Changelog

All notable changes to this project will be documented in this file.

This project follows [semantic versioning](https://semver.org/).

## 1.0.0 - 2026-08-23

First public release.

### Added

- Native Hermes `TTSProvider` integration for Inworld TTS-2 Flash and TTS-2.
- Native Hermes `TranscriptionProvider` integration for Inworld STT-1.
- Inworld Voices API discovery, with a built-in fallback list when the catalog is unavailable.
- MP3, WAV, OGG/Opus, and FLAC output support.
- Speaking-rate control, wired to Hermes' speed argument and clamped to Inworld's supported `[0.5, 1.5]` range.
- Delivery-mode, language, instruction, temperature, text-normalization, denoising, bit-rate, timeout, and retry configuration.
- `max_text_length` enforcement, reported to Hermes for chunking and clamped to Inworld's per-request ceiling.
- `max_file_bytes` cap on STT uploads, bounding the memory cost of a base64 JSON request body.
- Logging throughout under the `hermes_inworld_speech` logger, covering rejected config values, retries, voice-discovery fallback, and STT failures.
- Retry handling for transient failures (`408`, `429`, `5xx`) with exponential backoff, `Retry-After` support, and an overall wall-clock deadline of `timeout_seconds × max_attempts`.
- HTTPS enforcement on the `INWORLD_API_BASE_URL` override, so the credential cannot be redirected to a plaintext endpoint.
- Credential resolution from either the Hermes profile `.env` or the process environment, with a stray `Basic ` prefix stripped.
- Actionable failures on incompatible Hermes builds, at both import time and registration time.
- Docker deployment, troubleshooting, and release documentation.
- Test suite covering request shapes, retry behavior, credential resolution, config coercion, and output-path handling; lint and multi-version CI.
