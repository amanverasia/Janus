# Dashboard

Janus 3 includes **Cloudline**, a responsive control plane at `/dashboard/ui`.
It is a static SvelteKit 2, Svelte 5, and TypeScript application with modular
screens, responsive navigation, light/dark/system themes, and a searchable
command palette.

FastAPI serves the committed production bundle from
`src/janus/dashboard/static/app/`. Node.js and npm are development/build-time
dependencies only; a running Janus server does not need them. All Cloudline and
dashboard assets are served locally, so the interface has no runtime CDN dependency.

Open it in your browser:

```
http://localhost:20128/dashboard/ui
```

The root URL `/`, `/dashboard`, and former server-rendered page URLs redirect to
their matching Cloudline routes. Dashboard APIs remain under `/dashboard/api`.

## Authentication

Every dashboard client must authenticate with a valid Janus API key, including
clients on `127.0.0.1` and `localhost`. There is no loopback bypass and no
username/password login. Unauthenticated browser requests to either interface
are redirected to `/dashboard/login`, which sets an httponly
`janus_dashboard_key` cookie (30-day max-age) and returns to the originally
requested page. API-style requests without a valid key or cookie receive `401`.

DB-managed keys must be active and have **Allow dashboard login**
(`can_login=true`). Static API keys configured in YAML are also accepted. Manage
DB-key access from **API Keys**; the **Require API key** setting controls API
endpoint authentication and never makes the dashboard anonymous.

Accepted auth methods (same as the API):

- `Authorization: Bearer <key>`
- `x-goog-api-key: <key>`
- `?key=<key>`

!!! warning "Credential migration"
    Legacy dashboard username/password settings are removed during database
    initialization. Use an authorized API key to sign in after upgrading.

## Navigation

Cloudline's responsive sidebar groups its primary screens into three sections.
On narrow viewports it becomes a drawer, and the command palette (`Ctrl+K`,
`Cmd+K`, or `/`) can open any screen directly.

| Section | Pages |
|---|---|
| **Observe** | Overview, Usage, Analytics, Leaderboard, Request Logs |
| **Route** | Inventory, Providers, Combos, Routing, Token Savers |
| **Manage** | Budgets, API Keys, Tools, Pricing, Settings |

Inventory adds dedicated All Keys, Add Keys, and Import JSON screens. Cloudline
uses real deep links under `/dashboard/ui`, so browser navigation and bookmarks
work normally. The theme control cycles through system, light, and dark modes
and stores the preference in the browser.

---

## Observe

### Overview — `/dashboard/ui`

Summary landing page:

- Total requests, input/output tokens
- Provider and combo counts
- Today's total cost
- Global budget status bar

### Usage — `/dashboard/ui/usage`

- Live in-flight request count and recent gateway events
- Historical request volume, token use, and cost
- Automatic live-stream reconnection after transient disconnects

### Analytics — `/dashboard/ui/analytics`

- Spend trajectory for 7, 30, 90, or 365 days
- Breakdown by model, provider, account, or client key
- Request, token, cost, and success-rate summaries

### Leaderboard — `/dashboard/ui/leaderboard`

- Rank clients by tokens, cost, or requests
- Compare request volume, success rate, token use, and cost

### Request Logs — `/dashboard/ui/request-logs`

Debug view of captured API requests (**off by default** — enable **Request
Logging** under Settings, or set `server_request_logging=true`):

- Paginated table of recent requests: time, model, provider, status, and latency
- Per-request JSON detail (full request/response bodies, truncated at 64 KB)
- Successful completions (stream + non-stream), exhausted fallbacks (`503`), and
  non-fallback upstream errors (e.g. `400`) are recorded when logging is on
- Export all logs as JSON; Clear button wipes the table
- Retention is **configurable** via `server_request_log_retention` (default
  `500`, clamped between 50 and 5000) on the Settings page — oldest rows
  beyond the limit are pruned automatically

The legacy table also has a **User** column. It shows the DB-issued key name,
the configured static-key label (`client_key_label`), or `—` when an API request
was allowed without a client key.

If the page is empty, logging is almost always still disabled — check the banner
and the Settings toggle.

!!! warning "Sensitive content"
    Captured bodies contain prompts and completions. Leave request logging off
    unless actively debugging.

## Route

### Key Inventory — `/dashboard/ui/inventory`

Upstream key management — overview, key list, add, import, encryption status.
See [Key Inventory](inventory.md) for full documentation.

### Providers — `/dashboard/ui/providers`

Full CRUD for gateway providers:

- **Add / Edit** — set prefix, API type, base URL, API key, models, allowlists,
  and subscription quota
- **Test Connection** — 1-token probe with status and latency
- **Enable / Disable** — toggle without deleting
- **Delete** — remove provider (closes its HTTP client)

The provider workspace separates a logical provider prefix from its connections and
inventory accounts. Multiple enabled connections can share one prefix; Janus pools
their upstream accounts for fallback, cooldown, quota, and rate-limit-aware routing.
Custom models belong to that logical prefix, so deleting or disabling one connection
does not silently remove models that another same-prefix connection can serve.

Provider setup includes the catalog gallery and live **Fetch Models** helper.

When editing, leave the API key field **blank** to preserve the existing key.

Changes hot-reload — no server restart needed.

### Combos — `/dashboard/ui/combos`

Full CRUD for fallback chains:

- **Create / Edit** — name and ordered model list
- **Delete** — remove combo

### Routing — `/dashboard/ui/routing`

- Enabled provider and account readiness at a glance
- Current account strategy and try order
- Active cooldowns with remaining duration
- Quota-deprioritized accounts and a guarded clear-cooldowns action

### Token Savers — `/dashboard/ui/savers`

Toggle savers at runtime:

- **RTK** — on/off (default on)
- **Caveman** — on/off
- **Ponytail** — on/off with level selector (lite / full / ultra)
- **Headroom** — on/off with a configurable local proxy URL

Settings are stored in the DB and take effect immediately.

## Manage

### Budgets — `/dashboard/ui/budgets`

- **Budget list** — scope (global or key name), daily limit, spent today,
  percentage, status badge (`ok` / `warning` / `exceeded`)
- **Create** — select key scope, enter daily limit and warn percentage
- **Delete** — remove budget

### API Keys — `/dashboard/ui/keys`

- **Key list** — ID, prefix, name, login permission, model allowlist, status (active/revoked)
- **Create** — modal with **Allow dashboard login**, allowed models (`exact` or
  `prefix/*`), and daily budget; full `sk-janus-...` key shown **once**
- **Edit** — update name, dashboard access, models, or daily budget
- **Revoke** — deactivate key

### Tools — `/dashboard/ui/tools`

Copy-paste environment variable cards for:

- Claude Code
- Codex
- Cursor
- Cline

Each card shows the exact `export` commands for your server URL and auth settings.

### Pricing — `/dashboard/ui/pricing`

- View all ~28 builtin model prices
- **Add / Edit / Delete** custom pricing overrides
- Overrides merge with builtins at request recording time

### Settings — `/dashboard/ui/settings`

- **Require API key** — runtime toggle (stored in DB, overrides YAML default)
- **Enable account cooldowns** — when on (default), accounts that hit 429/5xx/auth/network
  errors are skipped until their cooldown expires. Turn off to override and keep
  retrying those accounts immediately (`server_cooldowns_enabled`). Also available
  via `janus settings set server_cooldowns_enabled false`
- **Sticky client routing** and account strategy
- **Reporting timezone** and request-log retention
- **Request Logging** — capture full request/response bodies for debugging (off by default), plus the log retention limit
- **Export secrets** — download current DB state as YAML after an explicit
  plaintext-credential warning

The legacy `/dashboard/settings` page additionally exposes the advanced combo
routing controls (`combo_strategy`, sticky limit, and Fusion tuning), server
information, and **Reset to Defaults**. See [Combos &
Fallback](combos.md#combo-strategies) for the routing fields. Values are
validated server-side, and reset wipes the relevant DB state before re-seeding
from `config.yaml`.

Settings does not contain a dashboard username or password. Dashboard identity
and access are API-key based, and **API Keys** is the place to grant or revoke
**Allow dashboard login**. Any legacy username/password settings are purged at
database initialization.

On **Routing**, use **Clear all cooldowns** to wipe active in-memory and SQLite
cooldown timers without changing the enable toggle.

---

## Management API

Cloudline reads authenticated, non-cacheable JSON state from the v2 API and
uses the existing dashboard endpoints for mutations. The v2 responses include
`section`, `alerts`, `data`, and `meta`; credentials and other sensitive fields
are removed before serialization.

| Method | Path | Action |
|---|---|---|
| `GET` | `/dashboard/api/v2/state/{section}` | Read state for a Cloudline screen |
| `POST` | `/dashboard/api/v2/keys` | Create an API key and return its plaintext value once |

Supported state sections are `overview`, `usage`, `analytics`, `leaderboard`,
`request-logs`, `inventory`, `inventory-keys`, `providers`, `combos`, `routing`,
`savers`, `budgets`, `keys`, `tools`, `pricing`, and `settings`. Every request
requires dashboard access; state and one-time credential responses use
`Cache-Control: private, no-store`.

The legacy management endpoints below default to HTML/HTMX responses. Endpoints
marked as JSON, and several endpoints used by Cloudline, also support JSON
responses. For scripting, prefer the [CLI](cli.md).

### API Keys

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/keys` | Create an API key (dashboard access/models/budget) |
| `POST` | `/dashboard/api/keys/{id}` | Update key scopes and optional daily budget |
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

Former server-rendered page routes under `/dashboard` redirect to the matching
screen names under `/dashboard/ui`; API routes are unchanged.
