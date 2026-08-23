# Optional: Inworld Router as the Hermes LLM backend

This speech plugin is independent of Hermes' LLM provider. Nothing here is required to use Inworld TTS/STT — you can pair the plugin with any Hermes LLM provider. It is recorded because the development setup also used Inworld Router for model inference, and the combination is a convenient one.

## Configure Inworld as a custom endpoint

In Hermes, add Inworld as a custom OpenAI-compatible endpoint:

```text
Base URL: https://api.inworld.ai/v1
API mode: chat_completions
```

## Prefer a router over a pinned model

A practical setup is to create an Inworld router (for example `hermes-main`) and let the router choose the underlying model:

```yaml
model:
  provider: custom:inworld.ai
  default: inworld/hermes-main
  api_mode: chat_completions
  max_tokens: 8192
```

Why a router instead of hard-coding one model? During development, direct model selection exposed model-specific compatibility constraints around reasoning and tool calling. Letting Inworld Router select a compatible model avoided coupling Hermes to those quirks while preserving tool calls.

## Credentials

The router uses the same Inworld credential as the speech plugin. Configuring one does not configure the other — Hermes reads its LLM credentials through its own model provider settings, while this plugin reads `INWORLD_API_KEY`. Set both if you use both.
