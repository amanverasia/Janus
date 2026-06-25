# Combos & Fallback

Combos are named, ordered model sequences. Instead of pinning a request to a
single provider, you send a combo name as the `model` field and Janus tries each
model in order — with automatic cooldowns and multi-account rotation.

## Defining a combo

Add combos to your `~/.janus/config.yaml`:

```yaml
combos:
  - name: best-effort
    models:
      - anthropic/claude-sonnet-4-20250514
      - openai/gpt-4o
      - gemini/gemini-2.5-pro
```

| Field | Type | Description |
|---|---|---|
| `name` | `string` | The combo name clients send as the `model` field |
| `models` | `list[string]` | Model identifiers in priority order (`prefix/model`) |

The order of `models` defines the fallback chain. The first model is tried
first; if it fails with a fallback-eligible error, the next is tried.

## Using combos from a client

Send the combo name as the `model` field — exactly like a normal model name:

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "best-effort",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

Janus resolves `best-effort` to its model chain and routes accordingly. Combo
names also appear in the `GET /v1/models` list.

## Fallback behavior

When a request arrives for a combo, Janus's `FallbackHandler` expands the chain
into a flat ordered list of attempts:

1. Each model in the combo is expanded to **all accounts** with that prefix.
2. Accounts currently in cooldown are **filtered out**.
3. Attempts are tried in order until one succeeds.

If all attempts are exhausted, the client receives `HTTP 503` with a
`All providers exhausted` error.

### Multi-account expansion

Each model in the combo expands to every registered account sharing that prefix.
If you have two OpenAI accounts (`openai-primary` and `openai-backup`), the entry
`openai/gpt-4o` generates two attempts — one per account.

```yaml
providers:
  - id: openai-primary
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY_1}
    models: [gpt-4o]

  - id: openai-backup
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY_2}
    models: [gpt-4o]
```

With this config, `openai/gpt-4o` yields two attempts. Register as many accounts
as you need under the same `prefix`.

## Cooldowns

When a provider returns an error, the account is placed in cooldown — removed
from the candidate pool for a fixed duration. Cooldowns are in-memory and based
on `time.monotonic()`, so they reset when the server restarts.

| Error type | Cooldown |
|---|---|
| Rate limit (429) | 60 seconds |
| Server error (5xx) | 30 seconds |
| Auth error (401, 403) | 300 seconds |
| Network error (timeout, connection) | 15 seconds |

An account in cooldown is skipped during attempt resolution. Once the cooldown
expires, the account is automatically available again.

## Fallback-eligible errors

Not every error triggers fallback. Janus classifies errors and only retries on
these:

| Error | Fallback-eligible? |
|---|---|
| `httpx.TimeoutException` | Yes |
| `httpx.ConnectError` | Yes |
| HTTP 429 | Yes |
| HTTP 401, 403 | Yes |
| HTTP 500 and above | Yes |
| Other 4xx (400, 404, 422, ...) | **No** |

A non-eligible error (e.g. `400 Bad Request`) is returned to the client
immediately — retrying on a different account won't help.

## Streaming

Streaming requests **do not retry mid-stream**. Once Janus commits to streaming
a response from a provider, it cannot replay partial output to a fallback.

This means:

- If the **initial connection** to a provider fails (timeout, connection error,
  or an error status code before any data is sent), Janus cools down that
  account and tries the next.
- Once the stream **starts successfully**, the response is returned as-is. If
  the stream breaks partway through, the client sees a truncated stream — no
  retry.

This is the expected behavior for all SSE-based AI streaming.
