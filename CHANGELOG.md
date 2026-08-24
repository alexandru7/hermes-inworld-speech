# Changelog

All notable changes to this project will be documented in this file.

This project follows [semantic versioning](https://semver.org/).

## 1.1.0 - 2026-08-24

### Added

- Streaming synthesis via `TTSProvider.stream()`, backed by Inworld's
  `POST /tts/v1/voice:stream` NDJSON endpoint. **Dormant on current Hermes builds:**
  `_dispatch_to_plugin_provider` calls `synthesize()` unconditionally, so nothing in
  core invokes `stream()` yet. Verified end to end against the live API so it works
  as soon as Hermes gains a streaming consumer.
- Streaming support for `mp3`, `wav`, `ogg`/`opus`, `flac`, `pcm`, and `linear16`.
- `tts.inworld.streaming` kill switch to force the batch path without downgrading.
- Per-encoding chunk handling: `PCM` and `LINEAR16` repeat a full RIFF/WAV header
  on every chunk, which is stripped after the first so concatenated audio does not
  click at chunk boundaries. `WAV` carries its header on the first chunk only and
  is passed through untouched.
- `sample_rate_hertz` is now validated against the rates Inworld actually accepts
  (8000, 16000, 22050, 24000, 32000, 44100, 48000) and snaps to the default with a
  warning instead of spending a request on a guaranteed rejection.

### Changed

- `synthesize()` and `stream()` share one payload builder, so generation options
  cannot drift between the batch and streaming paths.
- Streaming retries stop once the response opens: replaying a partially consumed
  stream would duplicate audio. Connection-time failures still retry as before.
- An `error` object arriving mid-stream is raised rather than being mistaken for a
  chunk, since the streaming endpoint can fail after HTTP 200 is already sent.

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
