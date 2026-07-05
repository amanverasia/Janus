# Providers

Janus routes requests to any of 40+ AI providers through a unified provider model.
Providers are registered via the **dashboard** (recommended) or seeded from
`config.yaml` on first startup. After seeding, the SQLite database is the source
of truth.

## Provider Model

Every provider entry has four key fields:

| Field | Description |
|-------|-------------|
| `api_type` | How Janus talks to the upstream — the wire protocol |
| `prefix` | The URL namespace for routing. Clients send `prefix/model` |
| `base_url` | The upstream API base URL |
| `models` | List of model suffixes this account supports |

The `prefix` is what makes Janus flexible. A client sends `"model": "openai/gpt-4o"`
and Janus looks up any provider registered with `prefix: openai`. If multiple
accounts share that prefix, Janus tries them in order with automatic fallback.

## Provider Types

| `api_type` | Use For |
|---|---|
| `openai_compat` | Any OpenAI-compatible API (OpenAI, Groq, Together, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, Qwen, ...) |
| `anthropic` | Direct Anthropic API |
| `gemini` | Direct Google Gemini API |
| `opencode_free` | OpenCode Zen free tier |
| `github_copilot` | GitHub Copilot subscription (OAuth device-code flow) |

Most providers use `openai_compat` — if a provider offers an "OpenAI-compatible"
endpoint, that's the one to use.

## Dashboard catalog

The dashboard **Providers** page includes a catalog of 15 known providers with
pre-filled defaults, logos (via Simple Icons), and one-click setup:

OpenAI, Anthropic, Google Gemini, Groq, Together AI, DeepSeek, OpenRouter,
Mistral, Fireworks, Perplexity, xAI, Qwen/DashScope, GitHub Copilot,
OpenCode Zen (Free), and Custom.

Each entry supports **Fetch Models** (auto-populate from upstream) and **Test
Connection** (1-token probe with latency).

## OAuth providers: GitHub Copilot

Copilot is Janus's first OAuth (subscription) provider — it authenticates with
your GitHub account instead of an API key:

1. Dashboard → Providers → Add Provider → pick **GitHub Copilot** from the catalog
   (or set API Type to `github_copilot`).
2. Click **Connect GitHub Account** — Janus starts GitHub's device-code flow and
   shows a one-time code.
3. Open the verification URL, enter the code, and authorize. The resulting OAuth
   token is filled into the API Key field automatically.
4. Click **Create Provider**. Use models as `copilot/gpt-4o` etc. (**Fetch
   Models** lists what your subscription includes.)

Under the hood, Janus stores the long-lived GitHub OAuth token and exchanges it
for short-lived Copilot session tokens automatically (refreshed before expiry
behind a single-flight lock) — no manual re-login needed. The Copilot chat
endpoint is OpenAI-compatible, so all formats, savers, combos, and fallback work
as usual.

!!! note "Terms of use"
    Routing Copilot through third-party tooling may be subject to GitHub's
    terms of service. Use with your own account at your own discretion.

## Subscription quotas

Any provider can be given a **quota window** — useful for subscription plans
(Copilot, Claude Pro-style 5-hour windows, monthly token allowances):

| Field | Values |
|---|---|
| Window | `5h` (fixed 5-hour buckets), `daily`, `weekly` (Mon–Sun), `monthly` — all UTC |
| Limit | Any positive integer |
| Metric | `requests` or `tokens` (input + output) |

Configure in the provider's Add/Edit form on the dashboard. The provider card
then shows a usage bar with the current window's consumption and a reset
countdown.

**Enforcement is soft:** when a provider's quota is exhausted, its accounts are
moved to the *end* of the fallback try-order (same mechanism as RPM/RPD rate
limits) — requests are never blocked, so a quota-exhausted provider still works
if it's the only option. In a combo, exhausting your subscription's quota means
the next tier is tried first until the window resets.

Consumption is counted from the `usage` table (shared across all inventory
accounts of the provider) and seeded on startup/reload, so restarts don't lose
window state.

## Inventory-backed routing

When [upstream key inventory](inventory.md) has routable keys for a gateway
prefix, Janus expands the provider into one account per key — the same
multi-account fallback behavior as registering multiple YAML providers with the
same prefix. The gateway provider's static `api_key` is used only when no routable
inventory keys exist.

Gateway prefix `gemini` maps to inventory provider `google`.

---

## Provider Setup

### Quick Reference

| Provider | `base_url` | `api_type` | Common Models |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `openai_compat` | gpt-4o, gpt-4o-mini, o3, o4-mini, gpt-4.1, gpt-4.1-mini |
| Anthropic | `https://api.anthropic.com` | `anthropic` | claude-sonnet-4-20250514, claude-opus-4-20250514, claude-3.5-sonnet-20241022 |
| Google Gemini | `https://generativelanguage.googleapis.com` | `gemini` | gemini-2.5-pro, gemini-2.0-flash, gemini-1.5-pro |
| Groq | `https://api.groq.com/openai/v1` | `openai_compat` | llama-3.3-70b-instruct, llama-3.1-405b-instruct |
| Together AI | `https://api.together.xyz/v1` | `openai_compat` | (various open-source models) |
| DeepSeek | `https://api.deepseek.com/v1` | `openai_compat` | deepseek-chat, deepseek-reasoner |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai_compat` | (hundreds of models via one API) |
| Mistral | `https://api.mistral.ai/v1` | `openai_compat` | mistral-large-2411 |
| Fireworks | `https://api.fireworks.ai/inference/v1` | `openai_compat` | (various open-source models) |
| Perplexity | `https://api.perplexity.ai` | `openai_compat` | (perplexity models) |
| xAI (Grok) | `https://api.x.ai/v1` | `openai_compat` | (Grok models) |
| Qwen / DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `openai_compat` | qwen-max, qwen-plus, qwen-turbo |
| OpenCode Zen | *(provided by service)* | `opencode_free` | (free tier models) |

---

### OpenAI

```yaml
providers:
  - id: openai
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini, o3, o4-mini, gpt-4.1, gpt-4.1-mini]
```

### Anthropic

```yaml
providers:
  - id: anthropic
    prefix: anthropic
    api_type: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-4-20250514, claude-opus-4-20250514, claude-3.5-sonnet-20241022]
```

### Google Gemini

```yaml
providers:
  - id: gemini
    prefix: gemini
    api_type: gemini
    base_url: https://generativelanguage.googleapis.com
    api_key: ${GEMINI_API_KEY}
    models: [gemini-2.5-pro, gemini-2.0-flash, gemini-1.5-pro]
```

### Groq

```yaml
providers:
  - id: groq
    prefix: groq
    api_type: openai_compat
    base_url: https://api.groq.com/openai/v1
    api_key: ${GROQ_API_KEY}
    models: [llama-3.3-70b-instruct, llama-3.1-405b-instruct]
```

### Together AI

```yaml
providers:
  - id: together
    prefix: together
    api_type: openai_compat
    base_url: https://api.together.xyz/v1
    api_key: ${TOGETHER_API_KEY}
    models: [meta-llama/Llama-3.3-70B-Instruct-Turbo, Qwen/Qwen2.5-72B-Instruct-Turbo]
```

### DeepSeek

```yaml
providers:
  - id: deepseek
    prefix: deepseek
    api_type: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    models: [deepseek-chat, deepseek-reasoner]
```

### OpenRouter

```yaml
providers:
  - id: openrouter
    prefix: openrouter
    api_type: openai_compat
    base_url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}
    models: [anthropic/claude-3.5-sonnet, openai/gpt-4o, google/gemini-2.0-flash-exp]
```

!!! tip
    OpenRouter gives you access to hundreds of models through a single API key.
    Use the full model IDs (e.g. `anthropic/claude-3.5-sonnet`) in the `models` list.

### Mistral

```yaml
providers:
  - id: mistral
    prefix: mistral
    api_type: openai_compat
    base_url: https://api.mistral.ai/v1
    api_key: ${MISTRAL_API_KEY}
    models: [mistral-large-2411]
```

### Fireworks

```yaml
providers:
  - id: fireworks
    prefix: fireworks
    api_type: openai_compat
    base_url: https://api.fireworks.ai/inference/v1
    api_key: ${FIREWORKS_API_KEY}
    models: [accounts/fireworks/models/llama-v3p1-405b-instruct]
```

### Perplexity

```yaml
providers:
  - id: perplexity
    prefix: perplexity
    api_type: openai_compat
    base_url: https://api.perplexity.ai
    api_key: ${PERPLEXITY_API_KEY}
    models: [sonar-pro, sonar-reasoning]
```

### xAI (Grok)

```yaml
providers:
  - id: xai
    prefix: xai
    api_type: openai_compat
    base_url: https://api.x.ai/v1
    api_key: ${XAI_API_KEY}
    models: [grok-3, grok-3-mini]
```

### Qwen / DashScope

```yaml
providers:
  - id: qwen
    prefix: qwen
    api_type: openai_compat
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: ${DASHSCOPE_API_KEY}
    models: [qwen-max, qwen-plus, qwen-turbo]
```

### OpenCode Zen

```yaml
providers:
  - id: opencode
    prefix: opencode
    api_type: opencode_free
    base_url: https://zen.opencode.ai
    api_key: ${OPENCODE_TOKEN}
    models: [sonnet, opus]
```

---

## Multi-Account Setup

Register multiple accounts with the **same `prefix`** but different `id` and
`api_key`. Janus treats them as a pool — when one account hits a rate limit or
error, it is cooled down and the next account is tried automatically.

```yaml
providers:
  - id: openai-personal
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY_1}
    models: [gpt-4o, gpt-4o-mini]

  - id: openai-work
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY_2}
    models: [gpt-4o, gpt-4o-mini, o3]

  - id: openai-third
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY_3}
    models: [gpt-4o, gpt-4o-mini]
```

All three accounts serve `openai/gpt-4o`. If `openai-personal` returns a 429,
Janus cools it down for 60 seconds and retries on `openai-work`, then
`openai-third`.

!!! note "Cooldown durations"
    | Error type | Cooldown |
    |------------|----------|
    | 429 (rate limit) | 60s |
    | 5xx (server error) | 30s |
    | Auth error | 300s |
    | Network error | 15s |

Combos work across providers too — see [Combos & Fallback](combos.md) for
cross-provider fallback chains.
