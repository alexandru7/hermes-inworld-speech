# Docker deployment

This document captures the deployment pattern used while developing `hermes-inworld-speech`.

## Tested topology

The important distinction is between the application directory and Hermes state:

```text
/opt/hermes   Hermes application/runtime
/opt/data     HERMES_HOME, config, sessions, plugins, .env
```

Confirm this in your container:

```bash
docker compose exec hermes sh -c '
  echo "user=$(whoami)"
  echo "home=$HOME"
  echo "HERMES_HOME=$HERMES_HOME"
  pwd
'
```

## Recommended Compose layout

```yaml
services:
  hermes:
    build: .
    container_name: hermes
    environment:
      INWORLD_API_KEY: ${INWORLD_API_KEY}
    volumes:
      - hermes-data:/opt/data
      - ./hermes-inworld-speech:/opt/data/plugins/inworld-speech:ro

volumes:
  hermes-data:
```

Why use a bind mount for the plugin?

- It survives container recreation.
- It is independent of the upstream Hermes image.
- You can update plugin source without rebuilding the entire Hermes image.
- It avoids baking files into `/opt/data`, which is commonly shadowed by a runtime volume.

## Why not `docker cp`?

`docker cp` is useful for a quick test:

```bash
docker cp ./hermes-inworld-speech hermes:/opt/data/plugins/inworld-speech
```

But copied files live in that specific container. A recreate/update may remove them. Use a bind mount for a maintained deployment.

## Dockerfile responsibilities

Use your Dockerfile for image-level software, not persistent Hermes state. For example:

```dockerfile
FROM nousresearch/hermes-agent:latest

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean
```

Add your distribution's usual package-list cleanup to that layer if you care about image
size.

Do not put the Inworld credential in the Dockerfile.

## Audio-system packages

This plugin only exchanges audio **files/data** with Inworld over HTTPS. It does not open a microphone or speaker itself.

Therefore these are not plugin dependencies:

- `libportaudio2`
- `portaudio19-dev`
- `libasound2-plugins`
- `pulseaudio-utils`

Install those only if the Hermes process inside the container must use server-side CLI voice capture/playback.

`ffmpeg` is optional but useful for transcoding and voice-message delivery workflows.

## Updating Hermes

A normal update flow can be:

```bash
docker compose pull
docker compose build --pull
docker compose up -d --force-recreate
```

Because the plugin is mounted from the host and `/opt/data` is persistent, the plugin and Hermes config survive the container replacement.
