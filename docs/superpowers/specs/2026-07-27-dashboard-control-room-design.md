# Dashboard Control Room Design

> **Date:** 2026-07-27
> **Status:** Approved — implementing

## Goal

Transform the Janus dashboard from a collection of stat pages into a **control room** that answers "am I okay?" at a glance, surfaces operational problems proactively, and polishes rough UX edges — without rewriting the HTMX/Jinja2 stack.

Three workstreams ship together via a shared alert foundation:

| Stream | Focus |
|--------|-------|
| **A — Control room** | Overview redesign: status strip, attention panel, live activity, setup checklist |
| **B — Deeper ops** | Routing live state, analytics trust flags, global budget banner |
| **C — UX polish** | Toast system, replace `alert()`, missing logos, nav/empty-state fixes |

## Decisions

| Topic | Choice |
|-------|--------|
| Frontend stack | Keep HTMX + Jinja2 + Tailwind CDN + Chart.js — no React/SPA |
| Alert architecture | Single `collect_dashboard_alerts()` module; all banners/panels consume it |
| Global banner | Rendered server-side via shared `_dashboard_context()` on every page route |
| Overview live widget | Poll JSON snapshot endpoint every 5s — not a persistent SSE connection |
| Routing live state | New `FallbackHandler.routing_snapshot()` + HTMX partial poll every 8s |
| Copilot cost view | v1: label subscription traffic in analytics; optional API-equivalent column deferred to v2 |
| Webhook notifications | Deferred — dashboard banners only in v1 |
| CDN bundling | Deferred — separate infra task |
| Low-credit threshold | `$1.00` credits_remaining for inventory keys with billing_model != subscription |
| Alert cap | Max 8 items in attention panel; severity-sorted |
| Inventory alert statuses | `critical`, `exhausted`, `invalid` upstream key statuses |

## Alert model

```python
@dataclass(frozen=True)
class DashboardAlert:
    id: str           # stable dedup key, e.g. "budget:global"
    severity: Literal["info", "warning", "critical"]
    title: str        # short headline
    detail: str       # one-line explanation
    href: str         # deep link into dashboard
```

### Severity ordering (highest first)

1. **critical** — budget exceeded, quota exhausted, no enabled providers, all keys invalid
2. **warning** — budget ≥ warn_pct, quota ≥ 80%, cooldowns active, low credits, unpriced models
3. **info** — setup checklist items incomplete, first-run hints

### Alert sources

| Source | Condition | Link |
|--------|-----------|------|
| Budgets | `get_budget_status()` → `warning` or `exceeded` for global + per-key | `/dashboard/budgets` |
| Quotas | Provider quota `status in ("warning", "exhausted")` | `/dashboard/providers` |
| Cooldowns | `get_active_cooldowns()` count > 0 | `/dashboard/routing` |
| Inventory | Upstream keys with `status in ("critical", "exhausted", "invalid")` or `credits_remaining < 1.0` | `/dashboard/inventory/keys` |
| Unpriced models | `get_unpriced_models(days=30)` filtered by current pricing registry | `/dashboard/pricing` |
| Setup | `enabled_providers == 0` or `api_keys == 0` | respective manage pages |

`collect_dashboard_alerts(db_path, request)` returns:

```python
{
    "alerts": list[DashboardAlert],      # sorted by severity, capped at 8
    "summary": Literal["ok", "warning", "critical"],
    "counts": {"warning": int, "critical": int},
}
```

Fail-open: any exception in a single source is logged and skipped; other sources still run.

## A — Control room (Overview)

### Layout (top → bottom)

1. **Status strip** — horizontal chips:
   - System badge: green "All clear" / amber "Attention needed" / red "Action required"
   - In-flight count (from `LiveUsageBus.snapshot()`)
   - Today's spend
   - Budget chip (if configured)
   - Cooldown count
   - Quota warning count

2. **Attention panel** — only when `alerts` non-empty; uses `alerts_partial.html`

3. **Setup checklist** — shown when `stats.total_requests == 0` OR no enabled providers:
   - Add provider or inventory key → `/dashboard/providers`
   - Create Janus API key → `/dashboard/keys`
   - Copy tool config → `/dashboard/tools`
   - Send first request (auto-checks when `total_requests > 0`)

4. **Quick setup** — keep existing base URL copy card

5. **Live activity widget** — 2-column on lg:
   - Left: mini RPS sparkline (last 2 min, client-side from snapshot poll)
   - Right: last 5 requests from snapshot `recent`
   - Link to full Usage page

6. **Stats cards** — existing four cards (requests, tokens, providers)

7. **Today's spend + budget bar** — existing block

8. **Active combos** — existing block

### Live snapshot endpoint

`GET /dashboard/api/usage/snapshot` → JSON:

```json
{"inflight": 2, "recent": [{"model": "...", "status": 200, "ts": 1234.5, ...}]}
```

Derived from `get_bus().snapshot()` — same data as SSE initial frame, no streaming.

## B — Deeper ops

### Global budget banner

In `base.html`, above `{% block content %}`:

- When `alert_summary != "ok"`, render `alerts_banner.html` (compact strip)
- Shows top critical/warning message + "View all" link to Overview attention panel anchor

All dashboard page routes pass `global_alerts` via `_dashboard_context()`.

### Routing page enhancements

`FallbackHandler.routing_snapshot()` returns:

```python
{
    "account_strategy": str,           # from settings
    "rotation_counters": dict[str, int],
    "sticky": list[{"client_key": str, "account_id": str, "uses": int}],
    "combo_rotation": dict[str, int],
}
```

UI changes on `routing.html`:

- Mark first non-cooled account per prefix with **Next** badge
- HTMX poll: `hx-get="/dashboard/api/routing/partial" hx-trigger="every 8s"`
- JS interval ticks cooldown seconds down between polls
- Sticky routing table (when sticky entries exist): client key label → pinned account

### Analytics trust

On analytics page load:

- If filtered unpriced models exist → amber banner with count + link to Pricing
- Breakdown table: amber `Unpriced` badge when `cost == 0 and (input_tokens + output_tokens) > 0` and model not in pricing registry
- Pass `unpriced_models` list in analytics route context

Filter logic: `get_unpriced_models(days)` minus models where `pricing_registry.get(model)` is not None.

## C — UX polish

### Toast system (`base.html`)

```javascript
function janusToast(message, type) { /* type: success | error | warning */ }
```

- Fixed bottom-right container `#janus-toast-stack`
- Auto-dismiss after 4s; manual close button
- Global listener: `document.body.addEventListener('htmx:responseError', ...)`

Replace `alert()` calls in `providers.html` and `combos.html` with `janusToast(...)`.

### Logos

Add SVG files under `src/janus/dashboard/static/logos/`:

- `qwen.svg`, `opencode.svg`, `github-copilot.svg`, `custom.svg`

Wire in `janus/catalog.py` gateway entries where `logo` is currently empty.

### Nav fix

Add `{% block leaderboard_active %}{% endblock %}` to `leaderboard.html`.

### Empty states

Enhance zero-data blocks on Overview, Analytics breakdown, Inventory keys list with CTA buttons (pattern: centered gray text + blue link button).

## File map

| File | Role |
|------|------|
| `src/janus/dashboard/alerts.py` | **New** — alert collection |
| `src/janus/dashboard/context.py` | **New** — `_dashboard_context()` helper |
| `src/janus/dashboard/routes.py` | Wire context helper, snapshot endpoint, routing partial |
| `src/janus/routing/fallback.py` | Add `routing_snapshot()` |
| `src/janus/dashboard/templates/base.html` | Toast stack, global banner include |
| `src/janus/dashboard/templates/overview.html` | Control room layout |
| `src/janus/dashboard/templates/alerts_partial.html` | **New** — attention panel |
| `src/janus/dashboard/templates/alerts_banner.html` | **New** — compact global strip |
| `src/janus/dashboard/templates/routing_partial.html` | **New** — routable HTMX partial |
| `src/janus/dashboard/templates/analytics.html` | Unpriced banner + badges |
| `src/janus/dashboard/templates/providers.html` | Toast instead of alert |
| `src/janus/dashboard/templates/combos.html` | Toast instead of alert |
| `src/janus/dashboard/templates/leaderboard.html` | Nav active block |
| `tests/unit/dashboard/test_alerts.py` | **New** — alert unit tests |
| `tests/integration/test_dashboard.py` | Overview + snapshot + banner tests |

## Out of scope (v1)

- Webhook/email notifications at budget warn threshold
- Copilot API-equivalent cost column (v2)
- Bundling CDN assets for offline deploy
- True rolling 5h quota windows
- Monthly/rolling spend budgets
- Dismissable alerts persisted to session/DB

## Testing

- Unit: `collect_dashboard_alerts` with mocked DB fixtures for each alert type
- Integration: Overview renders status strip + attention panel when budget warning seeded
- Integration: `/dashboard/api/usage/snapshot` returns inflight + recent
- Integration: Analytics page shows unpriced banner when usage row has $0 cost + tokens
- Integration: Global banner appears on `/dashboard/providers` when budget exceeded

## Acceptance criteria

1. Opening Overview with a budget at 85% shows amber status strip + attention item linking to Budgets
2. Opening any dashboard page with critical alerts shows compact banner in base layout
3. Providers/Combos HTMX failures show toast, not browser `alert()`
4. Routing page highlights next-available account and refreshes cooldown timers
5. Analytics flags models missing from pricing registry
6. Fresh install (no providers) shows setup checklist on Overview
