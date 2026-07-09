# Dashboard

Janus includes a built-in web UI at `/dashboard`. It's an HTMX-powered,
dark-themed interface — no build step, no npm, no JavaScript framework. Tailwind,
HTMX, and Chart.js are loaded via CDN.

Open it in your browser:

```
http://localhost:20128/dashboard
```

The root URL `/` redirects here.

## Authentication

**Loopback clients** (`127.0.0.1`, `localhost`) access the dashboard without auth.

**Remote clients** must authenticate with a valid Janus API key. Unauthenticated
browser requests are redirected to `/dashboard/login`, which sets an httponly
`janus_dashboard_key` cookie (30-day max-age). API-style requests without a cookie
receive `401`.

Accepted auth methods (same as the API):

- `Authorization: Bearer <key>`
- `x-goog-api-key: <key>`
- `?key=<key>`

!!! warning "Remote access"
    When binding to `0.0.0.0` (Docker default), enable `require_api_key` and use
    the login page for dashboard access from other machines. See
    [Deployment](deployment.md).

## Navigation

The sidebar groups 13 pages into four sections:

| Section | Pages |
|---|---|
| **Monitor** | Overview, Usage, Analytics, Key Inventory |
| **Manage** | Providers, Combos, Token Savers, Budgets |
| **Access** | API Keys, Tool Setup |
| **System** | Pricing, Settings |

---

## Monitor

### Overview — `/dashboard`

Summary landing page:

- Total requests, input/output tokens
- Provider and combo counts
- Today's total cost
- Global budget status bar

### Usage — `/dashboard/usage`

- Total requests, input tokens, output tokens
- Per-model breakdown

### Request Logs — `/dashboard/request-logs`

Debug view of captured API requests (**off by default** — enable **Request
Logging** under Settings, or set `server_request_logging=true`):

- Table of recent requests: time, format, model, provider, status, duration
- Per-request JSON detail (full request/response bodies, truncated at 64 KB)
- Successful completions (stream + non-stream), exhausted fallbacks (`503`), and
  non-fallback upstream errors (e.g. `400`) are recorded when logging is on
- Export all logs as JSON; Clear button wipes the table
- Only the most recent 500 requests are kept (pruned automatically)

If the page is empty, logging is almost always still disabled — check the banner
and the Settings toggle.

!!! warning "Sensitive content"
    Captured bodies contain prompts and completions. Leave request logging off
    unless actively debugging.

### Analytics — `/dashboard/analytics`

Interactive Chart.js visualizations:

- **Spend trend** — daily cost over time
- **Breakdown** — by model, provider, account, or client key
- **Success rate donut** — 2xx vs 4xx vs 5xx

| Parameter | Default | Options |
|---|---|---|
| `days` | `30` | Any integer (e.g. `?days=7`) |
| `dimension` | `model` | `model`, `provider`, `account`, `client_key` |

### Key Inventory — `/dashboard/inventory`

Upstream key management — overview, key list, add, import, encryption status.
See [Key Inventory](inventory.md) for full documentation.

---

## Manage

### Providers — `/dashboard/providers`

Full CRUD for gateway providers:

- **Catalog gallery** — 14 known providers (OpenAI, Anthropic, Gemini, Groq,
  DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, Qwen, Together,
  OpenCode Zen, Custom) with logos and pre-filled defaults
- **Add / Edit** — set prefix, API type, base URL, API key, models
- **Fetch Models** — auto-populate models from upstream `/models` endpoint
- **Test Connection** — 1-token probe with status and latency
- **Enable / Disable** — toggle without deleting
- **Delete** — remove provider (closes its HTTP client)

When editing, leave the API key field **blank** to preserve the existing key.

Changes hot-reload — no server restart needed.

### Combos — `/dashboard/combos`

Full CRUD for fallback chains:

- **Create / Edit** — name and ordered model list
- **Drag-and-drop reorder** — Sortable.js for priority changes
- **Delete** — remove combo

### Token Savers — `/dashboard/savers`

Toggle savers at runtime:

- **RTK** — on/off (default on)
- **Caveman** — on/off
- **Ponytail** — on/off with level selector (lite / full / ultra)

Settings are stored in the DB and take effect immediately.

### Budgets — `/dashboard/budgets`

- **Budget list** — scope (global or key name), daily limit, spent today,
  percentage, status badge (`ok` / `warning` / `exceeded`)
- **Create** — select key scope, enter daily limit and warn percentage
- **Delete** — remove budget

---

## Access

### API Keys — `/dashboard/keys`

- **Key list** — ID, prefix, name, login permission, model allowlist, status (active/revoked)
- **Create** — HTMX form with optional dashboard login, allowed models (`exact` or `prefix/*`), and daily budget; full `sk-janus-...` key shown **once**
- **Edit** — update name, login, models, or daily budget
- **Revoke** — deactivate key

### Tool Setup — `/dashboard/tools`

Copy-paste environment variable cards for:

- Claude Code
- Codex
- Cursor
- Cline

Each card shows the exact `export` commands for your server URL and auth settings.

---

## System

### Pricing — `/dashboard/pricing`

- View all ~28 builtin model prices
- **Add / Edit / Delete** custom pricing overrides
- Overrides merge with builtins at request recording time

### Settings — `/dashboard/settings`

- **Require API key** — runtime toggle (stored in DB, overrides YAML default)
- **Request Logging** — capture full request/response bodies for debugging (off by default)
- **Server info** — host, port, data directory
- **Export Config** — download current DB state as YAML
- **Reset to Defaults** — wipe DB tables and re-seed from `config.yaml` (danger zone)

---

## Management API

Dashboard HTMX endpoints return HTML partials. JSON endpoints are noted.

### API Keys

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/keys` | Create an API key (optional login/models/budget) |
| `POST` | `/dashboard/api/keys/{id}` | Update key scopes / optional daily budget |
| `DELETE` | `/dashboard/api/keys/{id}` | Revoke an API key |

### Budgets

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/budgets` | Create or update a budget |
| `DELETE` | `/dashboard/api/budgets/{id}` | Delete a budget |

### Providers

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/providers` | Create provider |
| `PUT` | `/dashboard/api/providers/{id}` | Update provider |
| `DELETE` | `/dashboard/api/providers/{id}` | Delete provider |
| `POST` | `/dashboard/api/providers/fetch-models` | Fetch models from upstream (JSON) |
| `POST` | `/dashboard/api/providers/{id}/test` | Test connection (JSON) |

### Combos

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/combos` | Create combo |
| `PUT` | `/dashboard/api/combos/{id}` | Update combo |
| `DELETE` | `/dashboard/api/combos/{id}` | Delete combo |

### Settings & config

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/settings` | Update runtime settings (savers, require_api_key, request logging) |
| `GET` | `/dashboard/api/export` | Export DB config as YAML (JSON download) |
| `POST` | `/dashboard/api/reset` | Reset DB and re-seed from YAML |
| `GET` | `/dashboard/api/request-logs/export` | Export captured request logs as JSON |
| `GET` | `/dashboard/api/request-logs/{id}` | Full detail for one captured request |
| `DELETE` | `/dashboard/api/request-logs` | Clear all captured request logs |

### Pricing

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/pricing` | Create or update pricing override |
| `DELETE` | `/dashboard/api/pricing/{model}` | Delete pricing override |

### Inventory

See [Key Inventory — Push API](inventory.md#push-api) for `POST /dashboard/api/inventory/push`.

For scripting, prefer the [CLI](cli.md) over HTMX endpoints.
