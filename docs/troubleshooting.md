# Troubleshooting

## 0. Turn on plugin logging first

Most questions below are answered directly by the plugin's own logs. It logs under the `hermes_inworld_speech` logger and warns on rejected config values, retries, voice-discovery fallback, and STT failures:

```python
import logging

logging.getLogger("hermes_inworld_speech").setLevel(logging.DEBUG)
```

`DEBUG` additionally reports which credential source was used, the resolved request shape, and any output-path rewrite. The API key is never logged.

## 1. Confirm Hermes home

```bash
echo "$HERMES_HOME"
```

In the official Docker image this is commonly `/opt/data`.

## 2. Confirm the plugin is visible

```bash
hermes plugins list
```

Docker:

```bash
docker compose exec hermes hermes plugins list
```

## 3. Confirm the plugin directory

```bash
ls -la "$HERMES_HOME/plugins/inworld-speech"
```

Expected core files:

```text
__init__.py
hermes_inworld_speech/
plugin.yaml
```

## 4. Confirm the API key without exposing it

```bash
sh -c 'test -n "$INWORLD_API_KEY" && echo OK || echo MISSING'
```

Docker:

```bash
docker compose exec hermes sh -c \
  'test -n "$INWORLD_API_KEY" && echo OK || echo MISSING'
```

The plugin reads the Hermes profile first and falls back to the process environment, so either source works — but both must be visible to the process that actually runs Hermes. A host shell variable is not enough unless Compose passes it into the container.

## 5. Inspect provider config

```bash
sed -n '/^tts:/,/^[^ ]/p' "$HERMES_HOME/config.yaml"
sed -n '/^stt:/,/^[^ ]/p' "$HERMES_HOME/config.yaml"
```

Minimum useful configuration:

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
```

## 6. Static config-schema warnings

Hermes may print a warning that a plugin-specific key is unrecognized. If it also says the value was saved, check `config.yaml`. The runtime provider reads the saved values even when the current dashboard/CLI schema does not expose them.

## 7. A setting seems to have no effect

The plugin ignores invalid config values rather than failing the request, and logs a warning naming the key each time. Enable logging (step 0) and look for `Ignoring ...`.

Two settings are model-specific and are silently accepted by the API on the wrong model:

- `delivery_mode` works only on `inworld-tts-2`
- `temperature` is ignored on `inworld-tts-2`

The default model is `inworld-tts-2-flash`, where `temperature` applies and `delivery_mode` does not.

## 8. TTS format issues

Set an explicit output format while debugging:

```bash
hermes config set tts.output_format mp3
```

Supported formats are MP3, WAV, OGG/Opus, and FLAC. An unrecognized value falls back to MP3 and logs a warning.

The plugin requests Inworld's `WAV` encoding for `wav`, not `LINEAR16` — `LINEAR16` is raw headerless PCM, which most players reject when handed to them in a `.wav` file.

## 9. Streamed audio clicks, stutters, or will not play

Streaming uses a different endpoint and different chunk handling from
`synthesize()`. First, confirm the batch path is healthy by disabling streaming:

```bash
hermes config set tts.inworld.streaming false
```

If that fixes it, the problem is in the streaming path — please report it with the
format you were using. Enable `DEBUG` logging (step 0) and look for
`Inworld TTS stream complete: N chunk(s)` to confirm chunks arrived, and for any
`repeated a RIFF header` warning, which means the encoding framed its chunks
differently than documented.

Clicking at regular intervals specifically indicates repeated container headers
inside the audio. The plugin strips those for `PCM` and `LINEAR16`, which are the
encodings Inworld documents as repeating them; `mp3` is the most exercised
streaming format if you need a known-good baseline.

## 10. The output file is not where I expected

The plugin returns the path it actually wrote and Hermes should use that return value. It appends the format's extension unless the supplied path already carries an audio extension, in which case it replaces it. Paths containing dots (`reply_2026.08.23`) keep their full name — the extension is appended, never substituted into the middle.

## 11. PortAudio errors

PortAudio errors come from Hermes server-side CLI voice mode. They do not indicate that the Inworld HTTP provider is broken. Only install PortAudio if the machine/container running Hermes must access a local sound device.

## 12. HTTP 401/403

Check:

- `INWORLD_API_KEY` contains the Base64 credential
- it does not accidentally contain `Basic Basic ...`
- the API key has the needed permissions
- the same key works with a direct Inworld curl request

The plugin accepts either a plain Base64 value or a value prefixed with `Basic ` and normalizes the latter.

## 13. HTTP 429/5xx

The plugin retries `408`, `429`, and `5xx` up to `max_attempts` (default 3), with exponential backoff that honours `Retry-After` up to 10 seconds. Retries are also bounded by an overall deadline of `timeout_seconds × max_attempts` — about 90 seconds on the defaults — after which it gives up rather than stalling Hermes further.

Tune with:

```yaml
tts:
  inworld:
    timeout_seconds: 30
    max_attempts: 3

stt:
  inworld:
    timeout_seconds: 30
    max_attempts: 3
```

Persistent 429s usually mean an account rate limit or an exhausted balance rather than a plugin fault.

## 14. Voice list is unavailable

Voice discovery is optional. If Inworld's voice-list endpoint fails, the plugin logs a warning and falls back to a minimal built-in list so TTS can still be configured. Custom voice IDs can always be entered directly in config.

## 15. STT returns no transcript

Try a standard audio format with a header (WAV, MP3, FLAC, or OGG/Opus), and make sure the recording actually contains speech. Inworld recommends 16 kHz mono PCM for optimal quality when you control the recording format.

STT failures return an error envelope rather than raising, so check the logs or the returned `error` field for the specific cause.

## 16. STT rejects a large file

Audio is base64-encoded into a JSON request body, so a request costs roughly three times the file size in memory. The plugin caps this at 25 MiB by default. Split long recordings, or raise the cap deliberately:

```yaml
stt:
  inworld:
    max_file_bytes: 52428800
```

## 17. `INWORLD_API_BASE_URL` is rejected

The override must use `https`. Plain `http` is permitted only for loopback addresses, so the credential cannot be sent in cleartext to a remote host. Unset the variable to return to `https://api.inworld.ai`.
