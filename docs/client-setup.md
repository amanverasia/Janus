# Client Setup

Point your coding tools at Janus. The base URL is always `http://localhost:20128/v1` (adjust port if you changed it).

## Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled in your config:

```bash
export ANTHROPIC_API_KEY=sk-janus-yourkey
```

Claude Code sends Anthropic-format requests. Janus translates and routes them to any configured provider.

## OpenAI-Compatible Tools (Codex, Cursor, etc.)

```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled:

```bash
export OPENAI_API_KEY=sk-janus-yourkey
```

Tools send OpenAI-format requests to `POST /v1/chat/completions`. Janus translates and routes.

## Cline (VS Code)

In Cline settings:

1. Set **API Provider** to "OpenAI Compatible"
2. Set **Base URL** to `http://localhost:20128/v1`
3. Set **API Key** to your Janus key (or any value if `require_api_key` is off)

## Generic OpenAI-Compatible Clients

Any tool that accepts a custom OpenAI base URL works with Janus:

- **Base URL:** `http://localhost:20128/v1`
- **API Key:** Your Janus key (if auth enabled)
- **Model:** Use `prefix/model` format (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-20250514`) or a combo name

## Creating an API Key

```bash
janus keys create --name "my-key"
# Created key: sk-janus-a1b2c3d4...
# ID: 1  Name: my-key
```

The full key is shown once. Use it in the `Authorization: Bearer <key>` header or as your `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.

## Gemini-Native Tools

Point Gemini SDKs and tools at Janus. The base URL is `http://localhost:20128`
(the Gemini endpoint is mounted at the root, not under `/v1`):

```bash
export GOOGLE_GEMINI_BASE_URL=http://localhost:20128
export GEMINI_API_KEY=sk-janus-yourkey
```

Tools send requests to `POST /v1beta/models/{model}:generateContent`. Use the
`prefix/model` convention in the model path (e.g. `openai/gpt-4o`) so Janus can
route it — the upstream provider does not need to be Gemini.

If `require_api_key` is enabled, Janus accepts the standard Gemini auth styles:
the `x-goog-api-key` header or the `?key=` query parameter.

## Model Naming

Models are referenced as `{prefix}/{model}`:

| You send | Janus routes to |
|---|---|
| `openai/gpt-4o` | OpenAI provider, model `gpt-4o` |
| `anthropic/claude-sonnet-4-20250514` | Anthropic provider, model `claude-sonnet-4-20250514` |
| `gemini/gemini-2.5-pro` | Gemini provider, model `gemini-2.5-pro` |
| `best-effort` | Combo — tries each model in the combo chain |

See [Combos](combos.md) for fallback chain configuration.
