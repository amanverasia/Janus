# Dashboard

Janus includes a built-in web UI at `/dashboard`. It's an HTMX-powered,
dark-themed interface — no build step, no npm, no JavaScript framework. Tailwind,
HTMX, and Chart.js are loaded via CDN.

Open it in your browser:

```
http://localhost:20128/dashboard
```

## Pages

### Overview

**`/dashboard`**

A summary landing page showing:

- Key stats: total requests, total input/output tokens
- Provider count
- Registered combos
- Today's total cost
- Global budget status bar (with percentage and ok/warning/exceeded indicator)

### Providers

**`/dashboard/providers`**

Lists all registered providers with their configuration details:

- Provider ID and prefix
- API type (`openai_compat`, `anthropic`, `gemini`, `opencode_free`)
- Base URL
- Supported models

### Combos

**`/dashboard/combos`**

Lists all registered combos with their model chains. Each combo shows its name
and the ordered list of `prefix/model` entries that define the fallback sequence.

### API Keys

**`/dashboard/keys`**

Manage API keys:

- **Key list**: shows ID, prefix, name, and status (active/revoked) for each key
- **Create**: HTMX form to create a new key by name. The full `sk-janus-...` key
  is displayed **once** — copy it immediately.
- **Revoke**: HTMX button to revoke a key. Revoked keys are rejected on
  authentication.

### Usage

**`/dashboard/usage`**

Usage statistics:

- Total requests
- Total input tokens
- Total output tokens
- Per-model breakdown (requests, input tokens, output tokens)

### Analytics

**`/dashboard/analytics`**

Interactive analytics with Chart.js:

- **Spend trend chart** — daily cost over time
- **Breakdown** — by model, provider, account, or client key
- **Success rate donut** — 2xx vs 4xx vs 5xx response distribution

Query parameters control the time window and breakdown dimension:

| Parameter | Default | Options |
|---|---|---|
| `days` | `30` | Any integer (e.g. `?days=7`) |
| `dimension` | `model` | `model`, `provider`, `account`, `client_key` |

Example: spend by provider for the last 7 days:

```
/dashboard/analytics?days=7&dimension=provider
```

### Budgets

**`/dashboard/budgets`**

Budget management with live status:

- **Budget list**: each row shows scope (global or key name), daily limit,
  spent today, percentage, and status badge (`ok` / `warning` / `exceeded`).
- **Create**: HTMX form — select key scope (global or specific key), enter daily
  limit and warn percentage.
- **Delete**: HTMX button to remove a budget.

Budget status updates in real time as new requests are processed.

## Management API

The dashboard includes HTMX endpoints that return HTML partials (not JSON):

| Method | Path | Action |
|---|---|---|
| `POST` | `/dashboard/api/keys` | Create an API key |
| `DELETE` | `/dashboard/api/keys/{id}` | Revoke an API key |
| `POST` | `/dashboard/api/budgets` | Create or update a budget |
| `DELETE` | `/dashboard/api/budgets/{id}` | Delete a budget |

These are designed for HTMX-driven forms and not intended for programmatic use.
For scripting, use the [CLI](cli.md) instead.
