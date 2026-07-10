# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic/Gemini-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, ...) talk to, then translates and routes each request to
any of 29 built-in AI providers — or any OpenAI-compatible endpoint — without
either side needing to know the other exists.

## First-time setup

Janus needs Python **3.11+**. Everything lives under `~/.janus/` — a seed
`config.yaml` and a SQLite database (`janus.db`) that becomes the source of truth
after the first startup.

### 1. Install

**From PyPI (recommended):**

```bash
pip install janus-ai
```

**From source (development):**

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Create config

```bash
janus config-init
```

This writes `~/.janus/config.yaml`. Open it and add at least one provider with
your API keys. Environment variables in `${VAR}` form are resolved at startup:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

Example provider block:

```yaml
providers:
  - id: openai
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini]
```

You can also add providers later from the dashboard — no restart required.

### 3. Start the server

```bash
janus serve --port 20128
```

For access from other machines on your LAN or Tailscale:

```bash
janus serve --host 0.0.0.0 --port 20128
```

Janus serves **plain HTTP** only. Use `http://`, not `https://`, unless you put
a reverse proxy with TLS in front.

### 4. Verify

```bash
curl http://localhost:20128/v1/health
# {"status":"ok"}
```

Open the dashboard at [http://localhost:20128/dashboard](http://localhost:20128/dashboard).
The root URL `/` redirects there.

### 5. Configure via dashboard

On first startup, Janus imports `providers`, `combos`, `token_savers`, and
`pricing` from YAML into SQLite. **After that, the database is authoritative** —
editing YAML and restarting will not re-apply changes. Use the dashboard instead.

| Step | Where | What |
|---|---|---|
| Add providers | **Providers** | Pick from the catalog or add custom; fetch models, test connection |
| Create a client key | **API Keys** | `sk-janus-...` shown once — save it |
| Enable auth | **Settings** | Toggle **Require API key** (recommended for remote access) |
| Set dashboard login | **Settings → Dashboard Login** | Username + password for remote browser sign-in |
| Connect your tools | **Tool Setup** | Copy-paste env vars for Claude Code, Codex, Cursor, Cline |

**Dashboard access rules:**

- **localhost** — no sign-in required
- **Remote** (LAN, Tailscale, Docker on `0.0.0.0`) — sign in at `/dashboard/login`
  with your dashboard username/password or a Janus API key

Create a key from the CLI instead:

```bash
janus keys create --name "my-laptop"
```

### 6. Send a test request

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 50
  }'
```

List registered models:

```bash
curl http://localhost:20128/v1/models
```

### 7. Point your coding tool at Janus

**Claude Code / Anthropic tools:**

```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
export ANTHROPIC_API_KEY=sk-janus-yourkey   # if require_api_key is on
```

**Cursor / OpenAI Chat Completions tools:**

```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
export OPENAI_API_KEY=sk-janus-yourkey      # if require_api_key is on
```

**Codex CLI** uses `POST /v1/responses` — configure a provider in
`~/.codex/config.toml` with `wire_api = "responses"` (see
[Client Setup](https://amanverasia.github.io/Janus/client-setup/)).

Use `prefix/model` in requests (e.g. `openai/gpt-4o`,
`anthropic/claude-sonnet-4-20250514`) or a combo name like `best-effort`.

**📚 [Documentation](https://amanverasia.github.io/Janus/) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)**

## Docker

```bash
mkdir -p janus-data
janus config-init --path janus-data/config.yaml
# Edit janus-data/config.yaml — add providers and ${ENV_VAR} keys

# Optional: pass API keys via .env in the repo root
echo 'OPENAI_API_KEY=sk-...' >> .env

docker compose up -d
```

The image binds to `0.0.0.0:20128`. SQLite and config persist in `./janus-data/`.
After first startup, manage providers and settings from the dashboard — not by
editing YAML alone.

**Remote dashboard:** enable **Require API key** in Settings, create a Janus API
key, and set a dashboard username/password under **Dashboard Login**.

```bash
curl http://localhost:20128/v1/health
open http://localhost:20128/dashboard    # macOS; or visit in your browser
```

## Configuration

Janus reads YAML from `~/.janus/config.yaml` (or `--config`) with `${ENV_VAR}`
token resolution. Generate a template with `janus config-init`.

On **first startup only**, YAML seeds the SQLite database. Subsequent changes
should be made via the **dashboard** or **Export Config** / **Reset to Defaults**
on the Settings page.

```yaml
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false

providers:
  - id: openai
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini, o3, o4-mini]

  - id: anthropic
    prefix: anthropic
    api_type: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-4-20250514, claude-opus-4-20250514]

combos:
  - name: best-effort
    models: [anthropic/claude-sonnet-4-20250514, openai/gpt-4o]
```

### Supported Provider Types

| `api_type` | Use For |
|---|---|
| `openai_compat` | Any OpenAI-compatible API (OpenAI, Groq, Together, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, ...) |
| `anthropic` | Direct Anthropic API |
| `gemini` | Direct Google Gemini API |
| `github_copilot` | GitHub Copilot (device-code OAuth from the dashboard) |
| `opencode_free` | OpenCode Zen free tier |

### Known Provider Base URLs

| Provider | `base_url` |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Together AI | `https://api.together.xyz/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| Fireworks | `https://api.fireworks.ai/inference/v1` |
| Perplexity | `https://api.perplexity.ai` |
| xAI (Grok) | `https://api.x.ai/v1` |
| Qwen/DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

## Client Setup

See step 7 in [First-time setup](#first-time-setup) for the basics. Full guides:
[Client Setup](https://amanverasia.github.io/Janus/client-setup/). The dashboard
**Tool Setup** page (`/dashboard/tools`) generates copy-paste env vars for your
exact server URL and auth settings.

**Claude Code / Anthropic tools:**
```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
```

**Cursor / OpenAI Chat Completions tools:**
```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
export OPENAI_API_KEY=sk-janus-yourkey  # if require_api_key is on
```

**Codex CLI** speaks the Responses API (`POST /v1/responses`). Prefer a
`~/.codex/config.toml` provider with `wire_api = "responses"` and
`base_url = "http://localhost:20128/v1"` — see the docs link above.

**Ollama-only tools** use `OLLAMA_HOST=http://localhost:20128` (`/api/chat`,
`/api/generate`, `/api/show`, `/api/tags`). **Gemini-native tools** use
`GOOGLE_GEMINI_BASE_URL=http://localhost:20128`.

## Features

- **Multi-format inbound** — OpenAI Chat Completions, OpenAI Responses (`/v1/responses` for Codex CLI), Anthropic Messages, Gemini GenerateContent, and Ollama (`/api/chat`, `/api/generate`, `/api/show`, `/api/tags`)
- **Fallback routing** — multi-account rotation with cooldowns (429→60s, 5xx→30s, auth→300s, network→15s)
- **Rate-limit-aware rotation** — accounts at their per-minute or per-day request quota are tried last
- **Subscription quotas** — per-provider 5h / daily / weekly / monthly windows; near-limit banners and soft deprioritization in routing
- **Combos** — named ordered model sequences (e.g., `"model": "best-effort"`)
- **Token savers** — RTK compression (default ON), Caveman, Ponytail, and optional Headroom compression proxy
- **GitHub Copilot OAuth** — device-code connect from the dashboard; session tokens refreshed automatically
- **API key scopes** — API-only keys (`can_login`), model allowlists (`prefix/*`), optional daily budgets
- **Budgets** — daily spending limits per API key or global, with warn/block thresholds
- **Request logging** — opt-in debug capture of request/response bodies (Settings → Request Logs)
- **Analytics** — cost tracking, spend trends, success rates, per-model/provider/key breakdowns
- **Pricing** — builtin model prices, YAML/DB overrides, cache token rates
- **Dashboard** — HTMX UI at `/dashboard` with charts, budgets, usage, routing overview, and remote login
- **Upstream key inventory** — validate, monitor, and route through a multi-key pool for 29 providers (`/dashboard/inventory`)

## Upstream Key Inventory

Built-in dashboard for upstream provider API keys: health checks, credit tracking, and automatic routing through the best available key.

**Dashboard:** `http://127.0.0.1:20128/dashboard/inventory`

- Overview stats, paginated/sortable keys table, key detail modal, best-keys widget
- Add keys, bulk submit, import from Dashboard export JSON, re-identify misclassified keys
- Encryption at rest; routable keys wired into gateway fallback rotation
- Detected rate limits (RPM/RPD) deprioritize near-quota keys during routing
- Background recheck scheduler (twice daily by default)

| Variable | Purpose |
|---|---|
| `INVENTORY_ENCRYPTION_KEY` | Fernet key for encrypting upstream keys at rest |
| `INVENTORY_PUSH_TOKEN` | Auth token for `POST /dashboard/api/inventory/push` |
| `INVENTORY_SCHEDULER_ENABLED` | Set to `false` to disable background rechecks (default: `true`) |

```bash
janus inventory generate-encryption-key          # create Fernet key
janus inventory migrate export.json --verify     # import Dashboard export + summary
janus inventory verify                           # cutover verification summary
janus inventory encrypt-keys                       # encrypt plaintext keys in DB
```

## CLI Reference

| Command | Description |
|---|---|
| `janus serve` | Start the gateway server |
| `janus config-init` | Generate default config YAML |
| `janus config-path` | Print config file path |
| `janus keys create/list/update/revoke` | Manage API keys (scopes: `--no-login`, `--models`, `--daily-budget`) |
| `janus usage stats/cost/by-key` | Usage and cost reports |
| `janus budgets list/set/delete` | Manage spending budgets |
| `janus pricing list/show` | View model pricing |
| `janus inventory migrate/verify/encrypt-keys/generate-encryption-key` | Upstream key inventory and cutover |

## Development

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
python -m venv .venv
pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest

# Lint + typecheck
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/

# Start dev server
.venv/bin/janus serve --port 20128 --reload
```

## Tech Stack

Python 3.11+ / FastAPI / httpx / Pydantic v2 / aiosqlite / Jinja2 / HTMX / Chart.js

## License

[GPL-3.0](LICENSE) © 2026 Aman Verasia
