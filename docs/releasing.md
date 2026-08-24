# Release checklist

Use this checklist before publishing a release.

1. Bump the version in **both** places:
   - `hermes_inworld_speech/__init__.py` (`__version__`) — also drives the HTTP `User-Agent`
   - `plugin.yaml` (`version:`)

   `tests/test_version.py` fails if these drift apart or if the changelog has no matching entry.

2. Add the release notes to `CHANGELOG.md` under a `## <version>` heading.

3. Run the full check:

   ```bash
   python -m pip install pytest ruff
   ruff check .
   ruff format --check .
   python -m compileall -q .
   python -m pytest
   ```

4. Test against a current Hermes Agent container with a real Inworld API key.

5. Verify:
   - plugin discovery and enable/disable
   - TTS with `inworld-tts-2-flash`
   - TTS with `inworld-tts-2`
   - at least one custom voice ID
   - each output format actually plays (`mp3`, `wav`, `ogg`, `flac`)
   - a non-default speaking rate audibly changes the output
   - **streaming**: for each of `mp3`, `wav`, `ogg`/`opus`, `flac`, `pcm`, and
     `linear16`, concatenate the streamed chunks and confirm the result decodes
     cleanly and plays without clicks at chunk boundaries:

     ```bash
     ffmpeg -v error -i streamed.out -f null -
     ```

     `ogg`/`opus` and `flac` deserve particular attention — Inworld documents
     per-chunk header behavior for `PCM`/`LINEAR16` and `WAV` but not for these,
     and no official Inworld example exercises them. Watch the logs for
     `repeated a RIFF header`, and update `_PER_CHUNK_RIFF_ENCODINGS` in
     `providers.py` if reality differs from the documentation.
   - streaming falls back correctly: set `tts.inworld.streaming: false` and
     confirm audio still plays via `synthesize()`
   - STT with WAV or MP3 input
   - missing/invalid credential failure behavior
   - Docker environment injection, with the key **absent** from the profile `.env`

6. Confirm no secrets or captured user audio are committed:

   ```bash
   git grep -nE 'INWORLD_API_KEY=.+|Authorization: Basic [A-Za-z0-9+/=]{16,}' -- . ':!docs/releasing.md'
   ```

7. Tag the release:

   ```bash
   git tag -s v1.0.0 -m 'v1.0.0'
   git push origin v1.0.0
   ```

8. In the GitHub release notes, call out the minimum Hermes compatibility tested for that release. The manifest cannot express this yet, so the release notes are the only place users will find it.

## Versioning

Use semantic versioning:

- patch: bug fixes and documentation-only improvements
- minor: backwards-compatible features/configuration
- major: provider name, config layout, or behavior changes that require user migration
