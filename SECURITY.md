# Security policy

## Reporting a vulnerability

Please report security issues privately to the repository maintainer, using GitHub Private Vulnerability Reporting, rather than opening a public issue with exploit details or credentials.

Expect an initial acknowledgement within a week. This is a community plugin maintained on a best-effort basis; there is no commercial support commitment.

## Supported versions

Fixes are released against the latest published version only.

## Secrets

`INWORLD_API_KEY` is sensitive. Do not:

- commit it to Git
- embed it in a Dockerfile with `ENV` or `ARG`
- paste it into issue reports or logs
- print the full container environment while troubleshooting

Prefer environment injection, Docker secrets, or the Hermes profile `.env` file with restrictive filesystem permissions.

The plugin never logs the credential. HTTP error bodies from Inworld are truncated to 4 KiB before being surfaced, to bound what an upstream error can splice into logs.

## Network behavior

By default the plugin sends requests only to `https://api.inworld.ai`.

`INWORLD_API_BASE_URL` overrides that base URL and is intended for tests and controlled proxies. The plugin requires the override to use `https`, permitting plain `http` only for loopback addresses (`localhost`, `127.0.0.1`, `::1`), so the credential cannot be silently redirected to a plaintext or third-party endpoint. Anyone able to set environment variables for the Hermes process can still point it at an arbitrary HTTPS host — treat write access to the Hermes environment as equivalent to holding the key.

## Audio data

STT audio is sent to Inworld for transcription. The plugin does not persist, cache, or log audio content; TTS output is written only to the path Hermes supplies. Inworld's own retention policy governs what happens to the data after it leaves the process.
