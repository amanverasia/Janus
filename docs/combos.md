# Combos & Fallback

Combos are named, ordered model sequences. Instead of pinning a request to a
single provider, you send a combo name as the `model` field and Janus tries each
model in order — with automatic cooldowns and multi-account rotation.

## Defining a combo

Create combos from the **dashboard** (recommended) or seed them in
`~/.janus/config.yaml` on first startup:

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

Use the dashboard **Combos** page to create, edit (with drag-and-drop reorder),
or delete combos at runtime.

## Combo strategies

By default a combo is a **fallback** chain — models are tried in order, each
falling forward to the next on a fallback-eligible error. Two other strategies
are available, set globally on the dashboard **Settings** page under **Combo
Routing** (or via `janus settings set combo_strategy <value>`):

| Strategy | Setting value | Behavior |
|---|---|---|
| Fallback *(default)* | `fallback` | Try each model in order until one succeeds |
| Round robin | `round_robin` | Rotate across the combo's models, staying on each for `combo_sticky_limit` requests before advancing |
| Fusion | `fusion` | Fan the request out to every combo member in parallel and have a judge model synthesize one answer — see [Fusion](#fusion) below |

`combo_sticky_limit` (default `1`) controls how many requests round-robin
stays on the current member before rotating to the next. A limit of `1`
rotates every request; higher values keep several consecutive requests on the
same model (useful for cache-friendly session affinity).

The strategy applies to every combo — there is currently no per-combo
override.

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

## Fusion

Fusion is a port of 9router's model-fusion combo strategy: instead of trying
combo members one at a time, Janus fans the request out to **all** of them in
parallel, then has a **judge** model synthesize one authoritative answer from
their responses.

### How it works

1. **Panel fan-out.** The combo's models become a *panel*. Each one receives
   the same request, non-streaming, with tools stripped and any tool-call
   history flattened into plain assistant text (so panel models keep
   conversational context without needing tool support).
2. **Quorum + straggler grace.** Once at least `combo_fusion_min_panel` panel
   members have answered, Janus waits a short **straggler grace** window
   (`combo_fusion_straggler_grace_s`) for any remaining models to finish, then
   cancels whatever is still pending. A **hard timeout**
   (`combo_fusion_hard_timeout_s`) bounds the whole panel call regardless of
   quorum, so one hung model can never stall the request indefinitely.
3. **Anonymized judge synthesis.** Successful panel answers are anonymized as
   `[Source 1]`, `[Source 2]`, ... and handed to a judge model, which is
   instructed to analyze consensus, contradictions, partial coverage, and
   blind spots across the sources, then write one final answer addressed
   directly to the user — without revealing that multiple models were
   involved. The judge call keeps the client's original `stream` flag and
   tools, and rides the normal fallback machinery like any other request.
4. **Judge selection.** The judge is the model set in `combo_fusion_judge`,
   falling back to the first model in the combo if unset. Janus validates
   that the judge can actually route **before** spending any panel tokens; if
   the configured judge can't route, it falls back to the first panel model
   that resolves. If the judge later becomes unavailable after the panel has
   already answered (e.g. it went into cooldown mid-request), Janus falls
   back again to the first *answering* panel model as judge rather than
   discarding the panel's work.
5. **Degraded cases.** If every panel model fails, the request returns
   `HTTP 503`. If exactly one panel model answers, Janus skips the judge
   entirely and returns that model's answer directly — there's nothing to
   synthesize from a single source.

If the client disconnects mid-panel, in-flight panel calls are cancelled and
awaited so no upstream spend is left running unattended.

### Tuning settings

Configured on the dashboard **Settings** page under **Combo Routing → Fusion**
(or `janus settings set <key> <value>`):

| Setting | Default | Description |
|---|---|---|
| `combo_fusion_judge` | *(empty)* | Judge model as `prefix/model`. Empty = first panel member |
| `combo_fusion_min_panel` | `2` | Minimum successful panel answers before starting the straggler-grace countdown (clamped to the panel size) |
| `combo_fusion_straggler_grace_s` | `8` | Seconds to wait for stragglers once quorum is reached |
| `combo_fusion_hard_timeout_s` | `90` | Absolute cap (seconds) on the whole panel call |

### Cost

Fusion multiplies upstream spend by the panel size — a 3-model combo run in
`fusion` mode makes up to 3 panel calls **plus** a judge call for every
request, compared to 1 call for `fallback`/`round_robin`. Use it for requests
where synthesized-quality matters more than cost, not as your default combo
strategy.

## Cooldowns

When a provider returns an error, the account is placed in cooldown — removed
from the candidate pool for a fixed duration. Cooldowns are stored in SQLite and
**persist across server restarts**.

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
