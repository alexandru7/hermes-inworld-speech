# Hermes Inworld Speech

A third-party [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that registers [Inworld AI](https://inworld.ai/) as both a text-to-speech (TTS) and speech-to-text (STT) provider.

The plugin uses Hermes' native provider extension points — `ctx.register_tts_provider()` and `ctx.register_transcription_provider()` — calls Inworld directly over HTTPS, and has no third-party Python runtime dependencies.

## Features

- Inworld TTS-2 Flash and TTS-2
- Inworld STT-1
- Streaming synthesis, so playback can start before the whole clip is rendered
- Inworld system and custom voice discovery, with a built-in fallback list
- MP3, WAV, OGG/Opus, and FLAC TTS output
- Speaking-rate, delivery-mode, and speaking-style control
- Configurable timeout and retry behavior, bounded by an overall deadline
- `INWORLD_API_KEY` from either the Hermes profile `.env` or the process environment
- No PortAudio requirement unless **Hermes itself** is using CLI/server-side microphone capture

## Requirements

- A Hermes Agent build that includes the `TTSProvider` and `TranscriptionProvider` plugin hooks. The plugin fails at load with an explicit message on older builds.
- An Inworld account and API key
- Network access from the Hermes process/container to `https://api.inworld.ai`
- Python 3.10 or newer

Inworld API keys are Base64 credentials used with HTTP Basic authentication. Store the credential as `INWORLD_API_KEY` **without** the literal `Basic ` prefix (a stray prefix is stripped for you).

## Installation

### Option A: install from a Git repository

```bash
hermes plugins install alexandru7/hermes-inworld-speech
hermes plugins enable inworld-speech
```

Or in one command install and enable:

```bash
hermes plugins install alexandru7/hermes-inworld-speech --enable
```

### Option B: install manually

Clone or copy this repository so that its `plugin.yaml` is directly inside the plugin directory:

```text
$HERMES_HOME/plugins/inworld-speech/
├── __init__.py
├── hermes_inworld_speech/
│   ├── __init__.py
│   ├── client.py
│   └── providers.py
└── plugin.yaml
```

Then enable it:

```bash
hermes plugins enable inworld-speech
```

For a standard local Hermes installation, `HERMES_HOME` is usually `~/.hermes`. In the official Hermes Docker image it is commonly `/opt/data`; confirm with:

```bash
echo "$HERMES_HOME"
```

## Authentication

Set `INWORLD_API_KEY` in whichever process actually runs Hermes. The plugin checks the active Hermes profile first, then the process environment, so either works.

Add the credential to the active Hermes profile `.env`:

```text
INWORLD_API_KEY=YOUR_BASE64_INWORLD_API_KEY
```

Alternatively, provide `INWORLD_API_KEY` through the environment of whatever process
starts Hermes — your shell profile, a systemd unit's `Environment=`, or your container
runtime. The plugin reads the profile `.env` first and falls back to the process
environment, so either source works.

For containers, inject the secret at runtime rather than baking it into an image:

```yaml
services:
  hermes:
    environment:
      INWORLD_API_KEY: ${INWORLD_API_KEY}
```

Put the value in the Compose project's `.env` file or another secret-management mechanism. Do **not** put the real key in a Dockerfile with `ENV` or `ARG`.

Verify the container sees the key without printing it:

```bash
docker compose exec hermes sh -c 'test -n "$INWORLD_API_KEY" && echo OK || echo MISSING'
```

## Configure Hermes

```bash
hermes config set tts.provider inworld
hermes config set tts.output_format mp3
hermes config set tts.inworld.model inworld-tts-2-flash
hermes config set tts.inworld.voice Dennis

hermes config set stt.enabled true
hermes config set stt.provider inworld
hermes config set stt.inworld.model inworld/inworld-stt-1
hermes config set stt.inworld.language auto
```

The resulting configuration looks like this — see [examples/config.yaml](examples/config.yaml) for a fuller example:

```yaml
tts:
  provider: inworld
  output_format: mp3
  inworld:
    model: inworld-tts-2-flash
    voice: Dennis

stt:
  enabled: true
  provider: inworld
  inworld:
    model: inworld/inworld-stt-1
    language: auto
```

Restart Hermes after enabling the plugin or changing provider selection.

> **Hermes config warning:** current Hermes releases may warn that plugin-specific keys such as `tts.inworld.model` or `stt.provider` are not present in the static config schema. Hermes still saves the values and the provider runtime reads them. This is a Hermes UI/schema limitation, not a plugin error.

## TTS configuration

Settings under `tts.inworld`:

| Setting | Default | Description |
|---|---|---|
| `model` | `inworld-tts-2-flash` | Inworld TTS model |
| `voice` | `Dennis` | System or custom Inworld voice ID |
| `max_text_length` | `2000` | Per-request text cap, reported to Hermes for chunking. Clamped to Inworld's 2000-character ceiling. |
| `sample_rate_hertz` | `24000` | Requested output sample rate |
| `speaking_rate` | unset (`1.0`) | Speech rate in `[0.5, 1.5]`. Hermes' own speed argument takes precedence; both are clamped to this range. |
| `bit_rate` | unset | Optional output bit rate |
| `language` | auto | Optional BCP-47 TTS language hint |
| `delivery_mode` | unset | `STABLE`, `BALANCED`, or `CREATIVE`. **`inworld-tts-2` only** — ignored by TTS-2 Flash. |
| `instruction` | unset | Optional speaking-style instruction |
| `temperature` | unset (`1.0`) | Expressiveness in `(0, 2]`. **Ignored by `inworld-tts-2`.** |
| `apply_text_normalization` | unset | `ON` / `OFF` |
| `enhance_generation` | unset | Optional denoising flag |
| `streaming` | `true` | Set `false` to disable streaming synthesis and force the batch path |
| `timeout_seconds` | `30` | Per-attempt HTTP timeout |
| `max_attempts` | `3` | Attempts for retryable HTTP/network failures |

`sample_rate_hertz` must be one of `8000`, `16000`, `22050`, `24000`, `32000`, `44100`, or `48000`. Anything else is rejected by Inworld, so the plugin warns and falls back to `24000` rather than spending a request on it.

`delivery_mode` and `temperature` apply to *different* models: `delivery_mode` works only on `inworld-tts-2`, which is also the model that ignores `temperature`. On the default `inworld-tts-2-flash`, `temperature` applies and `delivery_mode` does not.

Hermes' top-level `tts.output_format` controls the requested audio format. Supported values are `mp3`, `wav`, `ogg`, `opus`, and `flac`; `opus` is returned in an Ogg/Opus container (`.ogg`). An unrecognized value falls back to MP3 with a warning.

Use the higher-quality model, or a custom voice, with:

```bash
hermes config set tts.inworld.model inworld-tts-2
hermes config set tts.inworld.voice YOUR_VOICE_ID
```

Invalid configuration values are logged and ignored in favour of the default rather than failing the request — with one exception: text longer than `max_text_length` raises, because silently truncating speech is worse than a visible error.

## STT configuration

Settings under `stt.inworld`:

| Setting | Default | Description |
|---|---|---|
| `model` | `inworld/inworld-stt-1` | Inworld STT model |
| `language` | auto | Optional BCP-47 language hint; `auto` omits the hint |
| `max_file_bytes` | `26214400` (25 MiB) | Refuse audio larger than this |
| `timeout_seconds` | `30` | Per-attempt HTTP timeout |
| `max_attempts` | `3` | Attempts for retryable HTTP/network failures |

The plugin sends complete audio files to Inworld's synchronous STT endpoint using `AUTO_DETECT` audio encoding. Because the file is base64-encoded into a JSON body, a request costs roughly three times the file size in memory — hence the `max_file_bytes` cap. Inworld recommends 16 kHz mono PCM for optimal STT quality when you control the recording format.

## Streaming synthesis

The plugin implements Hermes' optional `TTSProvider.stream()` against Inworld's
`POST /tts/v1/voice:stream` endpoint, so audio starts arriving before the full clip is
rendered. Hermes uses it wherever it streams audio — voice-bubble delivery, for example —
and falls back to `synthesize()` automatically anywhere it doesn't.

Streaming is on by default and needs no configuration. Nothing changes for `synthesize()`.

Measured against the live API with a 146-character prompt on `inworld-tts-2-flash`, audio
began arriving in roughly 210–265 ms while the full clip took 610–770 ms — so playback
starts about two to three times sooner. `mp3` was consistently the slowest to first chunk
(~400 ms); the other formats were tightly grouped. Treat these as one sample from one
network, not a benchmark, and re-measure with
[`scripts/verify_streaming.py`](scripts/verify_streaming.py) if latency matters to you.

| Format | Encoding | Chunk framing |
|---|---|---|
| `mp3` | `MP3` | Container-framed; chunks concatenate directly |
| `wav` | `WAV` | Header on the first chunk only |
| `ogg` / `opus` | `OGG_OPUS` | Container-framed |
| `flac` | `FLAC` | Container-framed |
| `pcm` | `PCM` | Repeats a full RIFF header per chunk — stripped after the first |
| `linear16` | `LINEAR16` | Repeats a full RIFF header per chunk — stripped after the first |

That last distinction matters: Inworld returns `PCM` and `LINEAR16` chunks as complete
standalone WAV files so each can be played on its own. Concatenating them unmodified
produces an audible click at every chunk boundary, so the plugin strips the repeated
header. `WAV` behaves differently — one header, at the start — and is passed through
untouched.

If a format you request ever turns out to repeat container headers when it shouldn't, the
plugin strips them anyway and logs a warning naming the chunk. Please report that.

To disable streaming without downgrading the plugin:

```bash
hermes config set tts.inworld.streaming false
```

Streaming changes the failure model. Retries stop once the response opens, because
replaying a partially delivered stream would duplicate audio — so a connection dropped
mid-stream surfaces as an error rather than being retried. Connection-time failures
(`408`, `429`, `5xx`) still retry normally.

## Timeouts and retries

`timeout_seconds` bounds a single attempt. Retries are additionally bounded by an overall deadline of `timeout_seconds × max_attempts`, so the defaults cap a single logical request at about 90 seconds even when Inworld is both slow and rate limiting. `408`, `429`, and `5xx` are retried with exponential backoff, honouring `Retry-After` up to 10 seconds.

Retry backoff sleeps on the calling thread. Hermes invokes provider methods synchronously, so this is correct in the plugin's normal context — but if you reuse `InworldClient` from async code, run it in a worker thread.

## Logging

The plugin logs through the standard `logging` module under the `hermes_inworld_speech` logger. Warnings cover rejected config values, retries, voice-discovery fallback, and STT failures; `DEBUG` adds request shape and output-path decisions. To see everything while debugging:

```python
import logging

logging.getLogger("hermes_inworld_speech").setLevel(logging.DEBUG)
```

The API key is never logged.

## Docker deployment

Keep Hermes state in a volume and mount the plugin separately from the image:

```yaml
services:
  hermes:
    build: .
    environment:
      INWORLD_API_KEY: ${INWORLD_API_KEY}
    volumes:
      - hermes-data:/opt/data
      - ./hermes-inworld-speech:/opt/data/plugins/inworld-speech:ro

volumes:
  hermes-data:
```

This is preferable to `docker cp`, which writes into one container's writable layer so the plugin disappears when that container is recreated.

The plugin does **not** require PortAudio, ALSA, PulseAudio, or a sound device. Those matter only when the Hermes process itself captures or plays audio through server-side CLI voice mode.

See [docs/docker.md](docs/docker.md) for the full deployment guide, including the Dockerfile split and the update flow.

## Verify the plugin

```bash
hermes plugins list
hermes plugins enable inworld-speech
```

For Docker:

```bash
docker compose exec hermes hermes plugins list
```

If anything misbehaves, work through [docs/troubleshooting.md](docs/troubleshooting.md).

## Architecture

```text
Client / UI
    |
    v
Hermes Agent
    |-----------------------------|
    |                             |
    v                             v
Inworld STT                  Inworld TTS
/stt/v1/transcribe           /tts/v1/voice
    |                             |
    v                             v
transcript                   audio file
```

This plugin handles speech only. You can independently use Inworld Router as Hermes' LLM endpoint — see [docs/inworld-router.md](docs/inworld-router.md) — but that is not required for TTS/STT.

## Security

- Never commit `INWORLD_API_KEY`.
- Prefer runtime environment injection, Docker secrets, or the Hermes profile `.env` file.
- The plugin does not log the API key, and HTTP error bodies are truncated before being surfaced.
- `INWORLD_API_BASE_URL` is an advanced override for tests or controlled proxies. It must use `https` (plain `http` is permitted only for loopback addresses) so the credential cannot be redirected to an untrusted plaintext endpoint.

See [SECURITY.md](SECURITY.md).

## Development

```bash
python -m pip install pytest ruff
python -m pytest
ruff check .
```

The tests mock HTTP calls and do not require an Inworld account. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Compatibility notes

Hermes' TTS/STT provider hooks are relatively new and still evolving. This plugin targets the current `TTSProvider` / `TranscriptionProvider` contracts and deliberately avoids modifying Hermes internals. If the host build lacks those modules, the plugin raises an `ImportError` naming exactly what is missing.

Hermes currently prioritizes built-in providers and configured command providers ahead of a same-name plugin provider. This plugin uses the unique provider name `inworld`, so there is no built-in collision.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

This is a community plugin. It is not an official Inworld AI or Nous Research product unless those organizations explicitly adopt it.

## Upstream references

- Hermes Agent plugin system: https://github.com/NousResearch/hermes-agent
- Hermes TTS provider contract: `agent/tts_provider.py`
- Hermes STT provider contract: `agent/transcription_provider.py`
- Inworld TTS API: https://docs.inworld.ai/api-reference/ttsAPI/texttospeech/synthesize-speech
- Inworld STT API: https://docs.inworld.ai/api-reference/sttAPI/speechtotext/transcribe
- Inworld Voices API: https://docs.inworld.ai/api-reference/voiceAPI/voiceservice/list-voices
