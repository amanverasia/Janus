# API Reference

All endpoints are under the `/v1` prefix. Janus exposes two format-compatible APIs
(OpenAI and Anthropic) plus utility endpoints.

## Authentication

When `server.require_api_key` is `true` in your config, all endpoints except
`GET /v1/health` require an API key in the `Authorization` header:

```
Authorization: Bearer <key>
```

Keys can be:

- **Static** — listed in the `api_keys` section of `config.yaml`
- **DB-managed** — created via `janus keys create` or the dashboard

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
