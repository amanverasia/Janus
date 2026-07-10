# API Reference

Janus exposes five client-facing API surfaces plus utility endpoints:

| Surface | Base path | Format |
|---|---|---|
| OpenAI | `/v1/chat/completions` | OpenAI Chat Completions |
| OpenAI Responses | `/v1/responses` | OpenAI Responses API |
| Anthropic | `/v1/messages` | Anthropic Messages |
| Gemini | `/v1beta/models/{model}:generateContent` | Gemini GenerateContent |
| Ollama | `/api/chat`, `/api/generate`, `/api/show`, `/api/tags`, `/api/version` | Ollama chat & completions (NDJSON streaming) |
| Utility | `/v1/health`, `/v1/models` | JSON |

## Authentication

When `require_api_key` is enabled, all endpoints except `GET /v1/health` require
an API key. The setting can come from YAML (`server.require_api_key`) or the
dashboard Settings page (DB `server_require_api_key` — takes precedence when set).

Accepted auth methods:

```
Authorization: Bearer <key>
x-goog-api-key: <key>
?key=<key>
```

Keys can be:

- **Static** — listed in the `api_keys` section of `config.yaml` (always full access)
- **DB-managed** — created via `janus keys create` or the dashboard

DB keys support scopes:

- **Dashboard login** — `can_login` (default on). API-only keys authenticate for `/v1/*` but cannot open the dashboard.
- **Model allowlist** — exact IDs (`openai/gpt-4o`) and prefix wildcards (`openai/*`). Empty/unset means all models. Disallowed models return `403` with `error.type = "model_not_allowed"`. `GET /v1/models` is filtered the same way.
- **Daily budget** — optional per-key spend limit (see [Budgets](budgets.md)).

When `require_api_key` is `false`, no authentication is required (suitable for
local single-user setups).

---

## POST /v1/chat/completions

OpenAI Chat Completions format. Accepts the standard OpenAI request body and
returns standard OpenAI responses.

### Request Body

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | `prefix/model` (e.g. `openai/gpt-4o`) or a combo name |
| `messages` | `array` | Chat messages (`role` + `content`) |
| `stream` | `bool` | Stream the response via SSE (default `false`) |
| `max_tokens` | `int` | Maximum tokens to generate |
| `temperature` | `float` | Sampling temperature |
| `tools` | `array` | Tool/function definitions |
| `tool_choice` | `object` | Tool selection mode (`auto`, `none`, `required`, or specific) |

### Non-Streaming

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

Response — standard OpenAI `ChatCompletion` JSON:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  }
}
```

### Streaming

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100,
    "stream": true
  }'
```

Response — `text/event-stream` with `data: {json}\n\n` lines, terminated by
`data: [DONE]`:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

!!! note "Streaming and fallback"
    Streaming requests do **not** retry mid-stream. If the first provider fails
    before streaming begins, Janus falls back to the next account. Once streaming
    starts, it cannot be replayed.

---

## POST /v1/responses

OpenAI Responses API format — the native protocol of Codex CLI and newer OpenAI
SDK clients. Accepts the standard Responses request body and returns standard
Responses output; Janus translates to whatever format the upstream provider
speaks.

### Request Body

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | `prefix/model` or a combo name |
| `input` | `string \| array` | Prompt text, or a list of `message` / `function_call` / `function_call_output` items |
| `instructions` | `string` | System prompt |
| `stream` | `bool` | Stream via named SSE events (default `false`) |
| `max_output_tokens` | `int` | Maximum tokens to generate |
| `tools` | `array` | Flat function tool definitions (`{"type":"function","name",...}`) |
| `tool_choice` | `string \| object` | `auto`, `none`, `required`, or `{"type":"function","name":...}` |
| `reasoning` | `object` | `{"effort": "low" \| "medium" \| "high"}` |

`store` and `previous_response_id` are accepted but ignored — Janus is stateless.

### Non-Streaming

```bash
curl http://localhost:20128/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "openai/gpt-4o",
    "input": "Hello!"
  }'
```

Response — a `response` object with `output` items (`message`, `function_call`,
`reasoning`) and `usage`.

### Streaming

With `"stream": true`, the response is `text/event-stream` using named events
(`response.created`, `response.output_item.added`, `response.output_text.delta`,
`response.function_call_arguments.delta`, …, `response.completed`). There is no
`[DONE]` sentinel — the stream ends after `response.completed`.

---

## POST /v1/messages

Anthropic Messages format. Accepts the standard Anthropic request body and returns
standard Anthropic responses.

### Request Body

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | `prefix/model` (e.g. `anthropic/claude-sonnet-4-20250514`) or a combo name |
| `messages` | `array` | Chat messages (`role` + `content`) |
| `max_tokens` | `int` | Maximum tokens to generate |
| `system` | `string \| array` | System prompt (string or array of content blocks) |
| `stream` | `bool` | Stream the response via SSE (default `false`) |
| `tools` | `array` | Tool definitions |

### Non-Streaming

```bash
curl http://localhost:20128/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "anthropic/claude-sonnet-4-20250514",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Response — standard Anthropic `Message` JSON:

```json
{
  "id": "msg_...",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-20250514",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you?"
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 10,
    "output_tokens": 8
  }
}
```

### Streaming

Set `"stream": true`. The response is a `text/event-stream` with event-type-prefixed
SSE events:

```bash
curl http://localhost:20128/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "anthropic/claude-sonnet-4-20250514",
    "max_tokens": 100,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_...","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-20250514","usage":{"input_tokens":10,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}

event: message_stop
data: {"type":"message_stop"}
```

---

## POST /v1beta/models/{model}:generateContent

Gemini GenerateContent format. Point Gemini-native tools at Janus by using the
`prefix/model` routing convention in the URL path. Janus translates and routes
the request to any configured provider — the upstream does not need to be Gemini.

!!! note "Routing key"
    The `{model}` segment uses `prefix/model` (e.g. `openai/gpt-4o`) just like the
    other endpoints. This is what Janus uses for routing; the actual upstream model
    name is the part after the slash.

### Non-Streaming

```bash
curl "http://localhost:20128/v1beta/models/openai/gpt-4o:generateContent" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello!"}]}]
  }'
```

Response — standard Gemini `GenerateContentResponse` JSON:

```json
{
  "candidates": [
    {
      "content": {"role": "model", "parts": [{"text": "Hello! How can I help?"}]},
      "finishReason": "STOP",
      "index": 0
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 3,
    "candidatesTokenCount": 5,
    "totalTokenCount": 8
  }
}
```

### Streaming

Use `:streamGenerateContent` instead of `:generateContent`:

```bash
curl "http://localhost:20128/v1beta/models/openai/gpt-4o:streamGenerateContent" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello!"}]}]
  }'
```

The response is a `text/event-stream` of JSON objects, one per chunk.

!!! tip "Authentication"
    When `require_api_key` is on, Gemini-style auth is also accepted: the
    `x-goog-api-key` header or `?key=` query parameter.

---

## POST /api/chat (Ollama)

Ollama chat format — for tools that only support Ollama endpoints. Point the
tool's Ollama host at Janus (`http://localhost:20128`).

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | `prefix/model` or a combo name |
| `messages` | `array` | Chat messages (`system`/`user`/`assistant`/`tool`, `images` supported) |
| `stream` | `bool` | Defaults to **`true`** (Ollama convention); NDJSON chunks |
| `tools` | `array` | Function tool definitions (Ollama/OpenAI nested shape) |
| `options` | `object` | `num_predict`, `temperature`, `top_p`, `stop` |

```bash
curl http://localhost:20128/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

Streaming responses are `application/x-ndjson` — one JSON object per line,
ending with a `"done": true` object carrying `done_reason`,
`prompt_eval_count`, and `eval_count`.

---

## POST /api/generate (Ollama)

Ollama completion format — bare `prompt` instead of `messages`. Janus translates
to chat internally and remaps the response to Ollama's `generate` shape (`response`
field instead of `message`).

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | `prefix/model` or a combo name |
| `prompt` | `string` | Completion prompt |
| `stream` | `bool` | Defaults to **`true`**; NDJSON chunks with `response` deltas |
| `options` | `object` | Same as `/api/chat` (`num_predict`, `temperature`, etc.) |

```bash
curl http://localhost:20128/api/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{
    "model": "openai/gpt-4o",
    "prompt": "Hello!",
    "stream": false
  }'
```

Non-streaming responses include `"done": true` and `"response": "..."`. Streaming
uses `application/x-ndjson` with per-chunk `response` text and a final `"done": true`
line.

---

## POST /api/show (Ollama)

Model metadata handshake for Ollama clients. Accepts `name` (or `model`) and returns
stub metadata for routable models/combos; unknown or allowlist-blocked models return
`404`.

```bash
curl http://localhost:20128/api/show \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-janus-yourkey" \
  -d '{"name": "openai/gpt-4o"}'
```

Response includes `details`, `capabilities` (`completion`), and a minimal `template`.
`GET /api/tags` lists models in Ollama's tags shape; `GET /api/version` returns a
static version for client handshakes (no auth required). Both respect the API key
model allowlist when auth is enabled.

---

## GET /v1/models

Lists all registered provider models and combos.

```bash
curl http://localhost:20128/v1/models \
  -H "Authorization: Bearer sk-janus-yourkey"
```

Response:

```json
{
  "object": "list",
  "data": [
    {
      "id": "openai/gpt-4o",
      "object": "model",
      "created": 0,
      "owned_by": "openai"
    },
    {
      "id": "anthropic/claude-sonnet-4-20250514",
      "object": "model",
      "created": 0,
      "owned_by": "anthropic"
    },
    {
      "id": "best-effort",
      "object": "model",
      "created": 0,
      "owned_by": "combo"
    }
  ]
}
```

Provider models have `owned_by` set to the config `id`. Combos have `owned_by`
set to `"combo"`.

---

## GET /v1/health

Health check. No authentication required.

```bash
curl http://localhost:20128/v1/health
```

```json
{"status": "ok"}
```

---

## Error Responses

| Status | When | Details |
|--------|------|---------|
| `401` | Invalid API key | Returned when `require_api_key` is on and the key is missing or unrecognized |
| `429` | Budget exceeded | Daily spend limit reached. Includes a `Retry-After` header (seconds until midnight reset) |
| `503` | All providers exhausted | Every account in the fallback chain was tried and failed. `detail` contains the last error |

### 401 — Invalid API Key

```json
{"detail": "Invalid API key"}
```

### 429 — Budget Exceeded

```json
{
  "error": {
    "message": "Daily budget exceeded. Spent $5.23 of $5.00 limit. Resets at midnight.",
    "type": "budget_exceeded",
    "today_spend": 5.23,
    "daily_limit": 5.0
  }
}
```

With header: `Retry-After: 34567`

### 503 — All Providers Exhausted

```json
{"detail": "All providers exhausted: openai-personal: 429"}
```

---

## Inventory

### POST /dashboard/api/inventory/push

Programmatically ingest upstream API keys. Requires `INVENTORY_PUSH_TOKEN` as
Bearer auth. See [Key Inventory — Push API](inventory.md#push-api) for request
format and rate limits.
