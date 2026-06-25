# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, ...) talk to, then translates and routes each request to
40+ AI providers — without either side needing to know the other exists.

## Why Janus?

- **One endpoint, every provider** — point your tools at a single URL, route to OpenAI, Anthropic, Gemini, Groq, DeepSeek, and more
- **Automatic fallback** — if one provider is rate-limited or down, Janus rotates to the next automatically
- **Cost tracking** — per-request cost estimation with 28 builtin model prices and budget enforcement
- **Token savings** — RTK compression strips boilerplate from tool outputs before sending to the model
- **No cloud, no telemetry** — runs entirely on your machine, your keys never leave your system

## Quick Start

```bash
pip install janus
janus config-init
janus serve --port 20128
```

Point your coding tool at `http://localhost:20128/v1` and start routing.

!!! tip "What next?"
    - [Getting Started](getting-started.md) — full install and first-request walkthrough
    - [Configuration](configuration.md) — YAML config reference
    - [Providers](providers.md) — setup guides for all supported providers
    - [Client Setup](client-setup.md) — connect Claude Code, Codex, Cursor, and more

## Features

### Fallback Routing

Multi-account rotation with intelligent cooldowns. When a provider returns 429, 5xx, auth, or network errors, Janus automatically tries the next available account or model.

### Combos

Named ordered model sequences. A client sends `"model": "best-effort"` and Janus tries each model in the combo chain with all its accounts.

### Token Savers

- **RTK** (default ON) — compresses tool output (git diffs, file listings, logs) before sending
- **Caveman** — prepends a brevity-maximizing system prompt
- **Ponytail** — prepends a lazy-developer prompt (3 levels: lite, full, ultra)

### Budgets

Daily spending limits per API key or global. Warn at 80%, block at 100% with `429 + Retry-After`. Managed via CLI or dashboard.

### Analytics

Cost tracking, spend trends, success rates, and breakdowns by model, provider, account, or client key. Visualized in the dashboard with Chart.js.

### Dashboard

HTMX-powered dark-themed UI at `/dashboard`. Seven pages: Overview, Providers, Combos, API Keys, Usage, Analytics, Budgets.

## Tech Stack

Python 3.11+ / FastAPI / httpx / Pydantic v2 / aiosqlite / Jinja2 / HTMX / Chart.js

## License

Janus is licensed under the [GPL-3.0](https://github.com/amanverasia/Janus/blob/main/LICENSE) license.
