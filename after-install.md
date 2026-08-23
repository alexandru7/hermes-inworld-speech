# Inworld Speech installed

Enable the plugin, select `inworld` for TTS/STT, and restart Hermes.

```bash
hermes plugins enable inworld-speech

hermes config set tts.provider inworld
hermes config set tts.output_format mp3
hermes config set tts.inworld.model inworld-tts-2-flash
hermes config set tts.inworld.voice Dennis

hermes config set stt.enabled true
hermes config set stt.provider inworld
hermes config set stt.inworld.model inworld/inworld-stt-1
hermes config set stt.inworld.language auto
```

Set `INWORLD_API_KEY` in the Hermes environment or active profile `.env` — either works. The value is the Base64 API credential from Inworld; the `Basic ` prefix is stripped for you if you include it.

Hermes may warn that `tts.inworld.*` keys are not in its static config schema. The values are still saved and read at runtime.

Full configuration reference: [README.md](README.md). If something misbehaves, start with [docs/troubleshooting.md](docs/troubleshooting.md).
