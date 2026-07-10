# Client Setup

Point your coding tools at Janus. Most OpenAI/Anthropic clients use
`http://localhost:20128/v1` (adjust the port if you changed it). Gemini-native
and Ollama-native clients use the host root `http://localhost:20128`.

!!! tip "Tool Setup page"
    The dashboard at `/dashboard/tools` shows copy-paste environment variable
    cards for Claude Code, Codex, Cursor, and Cline — tailored to your server URL
    and auth settings. For Codex CLI, prefer the `config.toml` recipe below so
    requests hit `/v1/responses`.

## Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled in your config (or toggled from dashboard Settings):

```bash
export ANTHROPIC_API_KEY=sk-janus-yourkey
```

Claude Code sends Anthropic-format requests. Janus translates and routes them to any configured provider.

## Codex CLI (Responses API)

Codex CLI speaks the OpenAI **Responses** API (`POST /v1/responses`), not Chat
Completions. Janus exposes that endpoint natively.

Add a provider in `~/.codex/config.toml`:

```toml
model = "openai/gpt-4o"
model_provider = "janus"

[model_providers.janus]
name = "Janus"
base_url = "http://localhost:20128/v1"
env_key = "JANUS_API_KEY"
wire_api = "responses"
```

```bash
export JANUS_API_KEY=sk-janus-yourkey   # if require_api_key is on
```

`base_url` must include `/v1` — Codex appends `/responses`. Use any Janus
`prefix/model` or combo name as `model`. Streaming uses named SSE events
(`response.created` … `response.completed`); there is no `[DONE]` sentinel.

Older guides that only set `OPENAI_BASE_URL` may still work on some Codex
builds, but `config.toml` + `wire_api = "responses"` is the reliable path.

## Cursor and other Chat Completions tools

```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled:

```bash
export OPENAI_API_KEY=sk-janus-yourkey
```

Cursor, Cline (OpenAI Compatible mode), and similar tools send OpenAI Chat
Completions to `POST /v1/chat/completions`. Janus translates and routes.

In Cursor: set the OpenAI base URL / custom endpoint to
`http://localhost:20128/v1` and the API key to your Janus key. Pick models as
`prefix/model` (or a combo name) in the model picker when the tool allows custom
IDs.

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
# API Key (save this — shown once): sk-janus-a1b2c3d4...
# ID: 1  Name: my-key
# Login: yes
# Models: all
```

Optional scopes:

```bash
# API-only key (cannot open the dashboard), limited models, $5/day budget
janus keys create --name "agent" --no-login --models "openai/*,my-combo" --daily-budget 5
janus keys update 1 --login --clear-models   # restore full access later
```

The full key is shown once. Use it in the `Authorization: Bearer <key>` header or as your `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. Empty model allowlist means all models; patterns may be exact IDs or `prefix/*`.

## Gemini CLI and Gemini-native tools

Point Gemini SDKs, **Gemini CLI**, and other Gemini-native clients at Janus. The
base URL is the host root (`http://localhost:20128`) — the Gemini surface is
mounted at `/v1beta/...`, not under `/v1`:

```bash
export GOOGLE_GEMINI_BASE_URL=http://localhost:20128
export GEMINI_API_KEY=sk-janus-yourkey
```

Some clients use `GOOGLE_API_KEY` or `x-goog-api-key` instead of `GEMINI_API_KEY`;
Janus accepts the standard Gemini auth styles: `x-goog-api-key` header or the
`?key=` query parameter (when `require_api_key` is on).

Tools send requests to `POST /v1beta/models/{model}:generateContent` (and the
streaming `streamGenerateContent` variant). Use the `prefix/model` convention in
the model path (e.g. `openai/gpt-4o` or `gemini/gemini-2.5-pro`) so Janus can
route it — the upstream provider does not need to be Gemini.

**Cursor** typically uses the OpenAI Chat Completions path above, not this
Gemini surface. Use Gemini CLI / Google GenAI SDKs here.

## Ollama-Only Tools

Point Ollama-native clients at Janus (base URL `http://localhost:20128`, not under
`/v1`):

```bash
export OLLAMA_HOST=http://localhost:20128
```

If `require_api_key` is enabled:

```bash
export OLLAMA_API_KEY=sk-janus-yourkey
```

Clients use `POST /api/chat` (messages), `POST /api/generate` (bare prompt), or
`POST /api/show` (model metadata handshake). `GET /api/tags` lists routable models
(filtered by the key's model allowlist when scopes are set);
`GET /api/version` is available for client handshakes. Streaming defaults to
**on** with `application/x-ndjson` output.

## Model Naming

Models are referenced as `{prefix}/{model}`:

| You send | Janus routes to |
|---|---|
| `openai/gpt-4o` | OpenAI provider, model `gpt-4o` |
| `anthropic/claude-sonnet-4-20250514` | Anthropic provider, model `claude-sonnet-4-20250514` |
| `gemini/gemini-2.5-pro` | Gemini provider, model `gemini-2.5-pro` |
| `copilot/gpt-4o` | GitHub Copilot OAuth provider |
| `codex/o3` | ChatGPT Codex Responses API (when configured) |
| `claude/claude-sonnet-4-20250514` | Claude Code OAuth account (when configured) |
| `best-effort` | Combo — tries each model in the combo chain |

See [Combos](combos.md) for fallback chain configuration.

## Subscription / OAuth providers

GitHub Copilot supports **Connect GitHub Account** (device flow) in the Providers
UI. For Codex, Kiro, Antigravity/Gemini CLI, and Claude Code OAuth, paste either:

- a bare access token into the API key field, or
- a JSON credential blob:
  `{"access_token":"...","refresh_token":"...","expires_at":1710000000}`

When `refresh_token` is present Janus refreshes access tokens automatically
before expiry. Claude Code clients also get tool-dedupe + wire-shape
normalization when talking to Anthropic-format upstreams.

## Remote / Docker setups

When Janus runs on another machine or in Docker with `host: 0.0.0.0`, replace
`localhost` with the host's address. Enable `require_api_key` and use a
dashboard-created key. The dashboard requires login from non-loopback clients —
see [Dashboard — Authentication](dashboard.md#authentication).
