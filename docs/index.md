# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic/Gemini-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, ...) talk to, then translates and routes each request to
any of 29 built-in AI providers — or any OpenAI-compatible endpoint — without
either side needing to know the other exists.

## Why Janus?

- **One endpoint, every provider** — point your tools at a single URL, route to OpenAI, Anthropic, Gemini, Groq, DeepSeek, Copilot, and more
- **Automatic fallback** — if one provider is rate-limited or down, Janus rotates to the next automatically
- **Fusion combos** — fan a request out to a panel of models in parallel and synthesize one answer with a judge model
- **Key inventory** — store and route across many upstream API keys with validation, credit checks, and multi-account expansion
- **Cost tracking** — per-request cost estimation with builtin model prices, budgets, and subscription quota windows
- **Token savings** — RTK compression, optional Headroom proxy, and prompt savers before routing, with per-saver savings metrics on the dashboard
- **Per-provider model allowlists** — expose only selected models from a provider, by exact name or glob
- **Client-native surfaces** — Chat Completions, Responses (Codex), Anthropic, Gemini, and Ollama endpoints
- **Request-log user attribution** — captured requests show which API key made each call
- **No cloud, no telemetry** — runs entirely on your machine, your keys never leave your system

## Quick Start

```bash
pip install janus-ai
janus config-init
janus serve --port 20128
```

Point your coding tool at `http://localhost:20128/v1` and start routing.

!!! tip "What next?"
    - [Getting Started](getting-started.md) — full install and first-request walkthrough
    - [Configuration](configuration.md) — YAML config reference and DB-driven config
    - [Providers](providers.md) — setup guides for all supported providers
    - [Client Setup](client-setup.md) — connect Claude Code, Codex, Cursor, and more

## Features

### Fallback Routing

Multi-account rotation with intelligent cooldowns. When a provider returns 429, 5xx, auth, or network errors, Janus automatically tries the next available account or model. Cooldown state persists across restarts in SQLite.

### Combos

Named ordered model sequences. A client sends `"model": "best-effort"` and Janus tries each model in the combo chain with all its accounts.

### Key Inventory

Store, validate, and route with many upstream API keys across 27+ providers. Keys are auto-detected, rechecked on a schedule, and wired into gateway routing as multi-account pools. See [Key Inventory](inventory.md).

### Token Savers

- **RTK** (default ON) — compresses tool output (git diffs, file listings, logs) before sending
- **Caveman** — prepends a brevity-maximizing system prompt
- **Ponytail** — prepends a lazy-developer prompt (3 levels: lite, full, ultra)
- **Headroom** — optional external conversation compression proxy (`/v1/compress`), fail-open

### Client surfaces

OpenAI Chat Completions, OpenAI Responses (`/v1/responses` for Codex CLI), Anthropic Messages, Gemini GenerateContent, and Ollama (`/api/chat`, `/api/generate`, `/api/show`, `/api/tags`). See [Client Setup](client-setup.md).

### Budgets

Daily spending limits per API key or global. Warn at 80%, block at 100% with `429 + Retry-After`. Managed via CLI or dashboard.

### Analytics

Cost tracking, spend trends, success rates, and breakdowns by model, provider, account, or client key. Visualized in the dashboard with Chart.js.

### Dashboard

HTMX-powered dark-themed UI at `/dashboard`. Thirteen pages across four groups — Monitor (Overview, Usage, Analytics, Key Inventory), Manage (Providers, Combos, Token Savers, Budgets), Access (API Keys, Tool Setup), and System (Pricing, Settings). Full CRUD for providers, combos, savers, and pricing with hot-reload — no server restart needed.

## Tech Stack

Python 3.11+ / FastAPI / httpx / Pydantic v2 / aiosqlite / Jinja2 / HTMX / Chart.js

## License

Janus is licensed under the [GPL-3.0](https://github.com/amanverasia/Janus/blob/main/LICENSE) license.
