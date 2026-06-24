# Janus — Phase 5: Dashboard UI (Design Spec)

**Status:** Approved
**Date:** 2026-06-24
**Builds on:** Phases 1-4

---

## 1. Goal

Add a web dashboard at `http://localhost:PORT/dashboard` for managing API keys, viewing providers/combos, and checking usage stats. Server-rendered with HTMX for dynamic updates — no npm, no build step, ships in the pip package.

## 2. Tech

- **Jinja2** templates (server-rendered HTML)
- **HTMX** via CDN (dynamic partial updates, no full page reload)
- **Tailwind CSS** via CDN (styling)
- Served by FastAPI directly

## 3. Pages

| Route | Page | Description |
|-------|------|-------------|
| `/dashboard` | Overview | Total requests, input/output tokens, active providers, active combos |
| `/dashboard/providers` | Providers | Read-only list of configured providers with models and accounts |
| `/dashboard/combos` | Combos | Read-only list of combo names and their model sequences |
| `/dashboard/keys` | API Keys | Create/revoke keys via HTMX (calls `/api/keys/*`) |
| `/dashboard/usage` | Usage | Token stats per model, recent requests |

## 4. Management API (JSON, for HTMX)

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/keys` | List all keys (JSON) |
| `POST` | `/api/keys` | Create key (returns key + updated partial) |
| `DELETE` | `/api/keys/{id}` | Revoke key (returns updated partial) |
| `GET` | `/api/usage/stats` | Usage stats (JSON) |
| `GET` | `/api/providers` | Providers list (JSON) |

## 5. Architecture

New package: `src/janus/dashboard/`

```
src/janus/dashboard/
├── __init__.py
├── routes.py         # Dashboard + management API routes
├── templates/
│   ├── base.html     # Layout: nav + Tailwind + HTMX
│   ├── overview.html
│   ├── providers.html
│   ├── combos.html
│   ├── keys.html
│   └── usage.html
└── static/
    └── app.js        # Minor JS (HTMX config)
```

Registered in `app.py` alongside the `/v1` router.

## 6. Out of Scope

- Editing providers/combos via UI (YAML only)
- Dashboard authentication (local-first, localhost-bound)
- Charts/graphs (tables only for now)
- Real-time WebSocket updates (HTMX polling is fine)
