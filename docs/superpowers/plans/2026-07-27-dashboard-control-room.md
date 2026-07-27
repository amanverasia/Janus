# Dashboard Control Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a dashboard control room — shared alerts, Overview redesign, routing/analytics ops depth, and UX polish — on the existing HTMX/Jinja2 stack.

**Architecture:** Centralize alert collection in `dashboard/alerts.py`, inject via `dashboard/context.py` into all page templates, add lightweight JSON snapshot for Overview live widget, expose `FallbackHandler.routing_snapshot()` for Routing HTMX partial.

**Tech Stack:** FastAPI, Jinja2, HTMX 2.0.4, Tailwind CDN, Chart.js 4.4.1, aiosqlite, pytest + httpx ASGITransport

**Spec:** [`docs/superpowers/specs/2026-07-27-dashboard-control-room-design.md`](../specs/2026-07-27-dashboard-control-room-design.md)

## Global Constraints

- Keep HTMX + Jinja2 — no React/SPA rewrite
- Overview live widget polls JSON snapshot every 5s — no persistent SSE on Overview
- Alert list capped at 8 items; severity order: critical > warning > info
- Low-credit inventory threshold: `$1.00`
- Inventory alert statuses: `critical`, `exhausted`, `invalid`
- Fail-open alert collection: log and skip failed sources
- Copilot API-equivalent cost column deferred to v2
- Webhook notifications deferred to v1
- CDN bundling deferred

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/janus/dashboard/alerts.py` | `DashboardAlert` dataclass + `collect_dashboard_alerts()` |
| `src/janus/dashboard/context.py` | `_dashboard_context(request, db_path, **extra)` merges alerts into template dict |
| `src/janus/routing/fallback.py` | Add `routing_snapshot()` — read-only view of rotation/sticky state |
| `src/janus/dashboard/routes.py` | Snapshot endpoint, routing partial, migrate page handlers to context helper |
| `src/janus/dashboard/templates/alerts_banner.html` | Compact global strip |
| `src/janus/dashboard/templates/alerts_partial.html` | Full attention panel for Overview |
| `src/janus/dashboard/templates/routing_partial.html` | HTMX-swappable routing pools |
| `src/janus/dashboard/templates/base.html` | Toast stack + global banner slot |
| `src/janus/dashboard/templates/overview.html` | Control room layout |
| `tests/unit/dashboard/test_alerts.py` | Unit tests for alert collector |

---

### Task 1: Alert collector module

**Files:**
- Create: `src/janus/dashboard/alerts.py`
- Create: `tests/unit/dashboard/test_alerts.py`

**Interfaces:**
- Produces: `DashboardAlert` dataclass, `async def collect_dashboard_alerts(db_path: Path, request: Request) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/dashboard/test_alerts.py
import pytest
from pathlib import Path

from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.alerts import collect_dashboard_alerts, DashboardAlert
from janus.storage.database import init_db, seed_from_config
from janus.storage.budgets import create_or_update_budget


@pytest.fixture
async def db(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t", prefix="t", api_type="openai_compat",
                base_url="https://test.local/v1", api_key="k", models=["m1"],
            )
        ],
    )
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_from_config(db_path, cfg)
    return db_path


class _FakeRequest:
    app = type("App", (), {"state": type("S", (), {"pricing_registry": None})()})()


@pytest.mark.asyncio
async def test_budget_warning_alert(db):
    await create_or_update_budget(db, key_id=None, daily_limit=10.0, warn_pct=80)
    # seed spend above warn threshold via usage insert helper or direct SQL
    async with __import__("janus.storage.database", fromlist=["get_connection"]).get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO usage (cost, input_tokens, output_tokens, status) VALUES (9.0, 100, 50, 200)"
        )
        await conn.commit()
    result = await collect_dashboard_alerts(db, _FakeRequest())
    ids = [a.id for a in result["alerts"]]
    assert "budget:global" in ids
    assert result["summary"] in ("warning", "critical")


@pytest.mark.asyncio
async def test_no_providers_critical(db):
    async with __import__("janus.storage.database", fromlist=["get_connection"]).get_connection(db) as conn:
        await conn.execute("UPDATE providers SET is_enabled = 0")
        await conn.commit()
    result = await collect_dashboard_alerts(db, _FakeRequest())
    assert any(a.id == "setup:no_providers" for a in result["alerts"])
    assert result["summary"] == "critical"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/dashboard/test_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: janus.dashboard.alerts`

- [ ] **Step 3: Implement alerts.py**

```python
# src/janus/dashboard/alerts.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Request

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "critical"]
Summary = Literal["ok", "warning", "critical"]

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
_ALERT_CAP = 8
_LOW_CREDIT_USD = 1.0


@dataclass(frozen=True)
class DashboardAlert:
    id: str
    severity: Severity
    title: str
    detail: str
    href: str


async def collect_dashboard_alerts(db_path: Path, request: Request) -> dict[str, Any]:
    alerts: list[DashboardAlert] = []
    for collector in (
        _budget_alerts,
        _quota_alerts,
        _cooldown_alerts,
        _inventory_alerts,
        _unpriced_alerts,
        _setup_alerts,
    ):
        try:
            alerts.extend(await collector(db_path, request))
        except Exception:
            logger.exception("Dashboard alert collector %s failed", collector.__name__)
    alerts.sort(key=lambda a: (_SEVERITY_RANK[a.severity], a.id))
    alerts = alerts[:_ALERT_CAP]
    summary = _summarize(alerts)
    counts = {
        "critical": sum(1 for a in alerts if a.severity == "critical"),
        "warning": sum(1 for a in alerts if a.severity == "warning"),
    }
    return {"alerts": alerts, "summary": summary, "counts": counts}


def _summarize(alerts: list[DashboardAlert]) -> Summary:
    if any(a.severity == "critical" for a in alerts):
        return "critical"
    if any(a.severity == "warning" for a in alerts):
        return "warning"
    return "ok"
```

Implement each `_budget_alerts`, `_quota_alerts`, `_cooldown_alerts`, `_inventory_alerts`, `_unpriced_alerts`, `_setup_alerts` following the spec table. Reuse `_enrich_providers` quota logic from routes or import `get_routing_overview` quota_warnings.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/dashboard/test_alerts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/alerts.py tests/unit/dashboard/test_alerts.py
git commit -m "feat(dashboard): add centralized alert collector"
```

---

### Task 2: Dashboard context helper + global banner

**Files:**
- Create: `src/janus/dashboard/context.py`
- Create: `src/janus/dashboard/templates/alerts_banner.html`
- Modify: `src/janus/dashboard/templates/base.html`
- Modify: `src/janus/dashboard/routes.py` (overview + providers routes first, then remaining page routes)

**Interfaces:**
- Consumes: `collect_dashboard_alerts(db_path, request)`
- Produces: `async def dashboard_context(request: Request, db_path: Path, **extra: Any) -> dict[str, Any]`

- [ ] **Step 1: Write failing integration test**

```python
# append to tests/integration/test_dashboard.py
@pytest.mark.asyncio
async def test_global_alert_banner_on_providers_when_budget_exceeded(app, tmp_path):
    from janus.storage.budgets import create_or_update_budget
    db_path = app.state.db_path
    await create_or_update_budget(db_path, key_id=None, daily_limit=1.0, warn_pct=80)
    async with __import__("janus.storage.database", fromlist=["get_connection"]).get_connection(db_path) as conn:
        await conn.execute("INSERT INTO usage (cost, input_tokens, output_tokens, status) VALUES (2.0, 1, 1, 200)")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "budget" in r.text.lower() or "spend" in r.text.lower()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/integration/test_dashboard.py::test_global_alert_banner_on_providers_when_budget_exceeded -v`

- [ ] **Step 3: Implement context.py**

```python
# src/janus/dashboard/context.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from janus.dashboard.alerts import collect_dashboard_alerts


async def dashboard_context(request: Request, db_path: Path, **extra: Any) -> dict[str, Any]:
    alert_data = await collect_dashboard_alerts(db_path, request)
    return {
        "request": request,
        "global_alerts": alert_data["alerts"],
        "alert_summary": alert_data["summary"],
        "alert_counts": alert_data["counts"],
        **extra,
    }
```

- [ ] **Step 4: Add alerts_banner.html**

Compact strip: show highest-severity alert title + link; red/amber border matching severity; hide when `alert_summary == "ok"`.

- [ ] **Step 5: Update base.html**

Insert before `{% block content %}`:

```html
{% if alert_summary is defined and alert_summary != "ok" %}
  {% include "alerts_banner.html" %}
{% endif %}
```

Add toast stack container + `janusToast()` + `htmx:responseError` listener at end of `<body>`.

- [ ] **Step 6: Migrate all page route handlers in routes.py**

Replace manual `context = {"request": request, ...}` with `context = await dashboard_context(request, db_path, ...)`.

Affected handlers: `overview`, `providers_page`, `combos_page`, `routing_page`, `usage_page`, `analytics` route, `leaderboard_page`, `budgets_page`, `keys_page`, `tools_page`, `pricing_page`, `settings_page`, `request_logs_page`.

- [ ] **Step 7: Run tests — expect PASS**

Run: `pytest tests/integration/test_dashboard.py -v`

- [ ] **Step 8: Commit**

```bash
git add src/janus/dashboard/context.py src/janus/dashboard/templates/alerts_banner.html \
  src/janus/dashboard/templates/base.html src/janus/dashboard/routes.py \
  tests/integration/test_dashboard.py
git commit -m "feat(dashboard): global alert banner and toast foundation"
```

---

### Task 3: Overview control room redesign

**Files:**
- Create: `src/janus/dashboard/templates/alerts_partial.html`
- Modify: `src/janus/dashboard/templates/overview.html`
- Modify: `src/janus/dashboard/routes.py` (overview handler + snapshot endpoint)

**Interfaces:**
- Produces: `GET /dashboard/api/usage/snapshot` → `JSONResponse({"inflight": int, "recent": list})`

- [ ] **Step 1: Write failing integration tests**

```python
@pytest.mark.asyncio
async def test_overview_status_strip(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Status" in r.text or "All clear" in r.text

@pytest.mark.asyncio
async def test_usage_snapshot_endpoint(app):
    from janus.dashboard.live import get_bus, reset_bus
    reset_bus()
    get_bus().record_completed(model="t/m1", status=200, input_tokens=1, output_tokens=2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/usage/snapshot")
        assert r.status_code == 200
        data = r.json()
        assert "inflight" in data
        assert "recent" in data
        assert len(data["recent"]) >= 1
    reset_bus()
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add snapshot route**

```python
@router.get("/api/usage/snapshot")
async def usage_snapshot(_request: Request) -> JSONResponse:
    from janus.dashboard.live import get_bus
    snap = get_bus().snapshot()
    return JSONResponse({"inflight": snap["inflight"], "recent": snap["recent"][-5:]})
```

- [ ] **Step 4: Extend overview route context**

Add to overview handler via `dashboard_context`:

```python
live_snapshot = get_bus().snapshot()
setup_checklist = {
    "has_providers": provider_count > 0,
    "has_keys": len(await list_keys(db_path)) > 0,
    "has_requests": stats["total_requests"] > 0,
}
```

- [ ] **Step 5: Rewrite overview.html**

Implement layout per spec: status strip → attention partial → setup checklist (conditional) → quick setup → live widget (poll `/dashboard/api/usage/snapshot` every 5s with fetch + mini Chart.js sparkline) → stats → spend → combos.

Reuse Chart.js RPS logic from `usage.html` (copy the rolling bucket approach, simplified to ~24 bars).

- [ ] **Step 6: Create alerts_partial.html**

Render `global_alerts` as severity-colored list with icons and deep links.

- [ ] **Step 7: Run tests — expect PASS**

Run: `pytest tests/integration/test_dashboard.py::test_overview_status_strip tests/integration/test_dashboard.py::test_usage_snapshot_endpoint -v`

- [ ] **Step 8: Commit**

```bash
git add src/janus/dashboard/templates/overview.html src/janus/dashboard/templates/alerts_partial.html \
  src/janus/dashboard/routes.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): control room overview with live snapshot widget"
```

---

### Task 4: Routing live state

**Files:**
- Modify: `src/janus/routing/fallback.py`
- Create: `src/janus/dashboard/templates/routing_partial.html`
- Modify: `src/janus/dashboard/templates/routing.html`
- Modify: `src/janus/dashboard/routes.py`

**Interfaces:**
- Produces: `FallbackHandler.routing_snapshot() -> dict[str, Any]`
- Produces: `GET /dashboard/api/routing/partial` → HTML partial

- [ ] **Step 1: Write failing unit test**

```python
# tests/unit/routing/test_fallback_snapshot.py
def test_routing_snapshot_keys():
    from janus.routing.fallback import FallbackHandler
    from janus.providers.registry import ProviderRegistry
    fh = FallbackHandler(ProviderRegistry())
    fh._rotation_counters["openai"] = 2
    snap = fh.routing_snapshot()
    assert snap["rotation_counters"]["openai"] == 2
    assert "account_strategy" in snap
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Add routing_snapshot() to FallbackHandler**

```python
def routing_snapshot(self) -> dict[str, Any]:
    sticky = [
        {"client_key": k, "account_id": acc, "uses": uses}
        for k, (acc, uses) in self._sticky.items()
    ]
    return {
        "account_strategy": getattr(self, "_last_strategy", "round_robin"),
        "rotation_counters": dict(self._rotation_counters),
        "sticky": sticky,
        "combo_rotation": dict(self._combo_rotation),
    }
```

Store `_last_strategy` when `get_targets()` is called (one-line assignment at method entry reading from settings/default).

- [ ] **Step 4: Extract routing pool markup into routing_partial.html**

Move provider account `<ol>` from `routing.html` into partial. Mark first account where `not cooldown_active and not quota_deprioritized` with `<span class="...">Next</span>` badge.

- [ ] **Step 5: Add routing partial endpoint + HTMX poll on routing.html**

```python
@router.get("/api/routing/partial", response_class=HTMLResponse)
async def api_routing_partial(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    overview = await get_routing_overview(db_path)
    live = request.app.state.fallback_handler.routing_snapshot()
    ctx = await dashboard_context(request, db_path, overview=overview, routing_live=live)
    return _templates.TemplateResponse(request, "routing_partial.html", ctx)
```

Wrap pools div:

```html
<div id="routing-pools" hx-get="/dashboard/api/routing/partial" hx-trigger="load, every 8s" hx-swap="innerHTML">
  {% include "routing_partial.html" %}
</div>
```

- [ ] **Step 6: Add cooldown countdown JS**

On partial swap (`htmx:afterSwap`), start 1s interval decrementing `[data-cooldown-secs]` elements until next poll refreshes.

- [ ] **Step 7: Run tests**

Run: `pytest tests/unit/routing/test_fallback_snapshot.py tests/integration/test_dashboard.py -k routing -v`

- [ ] **Step 8: Commit**

```bash
git add src/janus/routing/fallback.py src/janus/dashboard/templates/routing_partial.html \
  src/janus/dashboard/templates/routing.html src/janus/dashboard/routes.py \
  tests/unit/routing/test_fallback_snapshot.py
git commit -m "feat(dashboard): routing live state with HTMX refresh"
```

---

### Task 5: Analytics trust flags

**Files:**
- Modify: `src/janus/dashboard/routes.py` (analytics handler)
- Modify: `src/janus/dashboard/templates/analytics.html`

- [ ] **Step 1: Write failing integration test**

```python
@pytest.mark.asyncio
async def test_analytics_unpriced_banner(app):
    db_path = app.state.db_path
    async with __import__("janus.storage.database", fromlist=["get_connection"]).get_connection(db_path) as conn:
        await conn.execute(
            "INSERT INTO usage (model, cost, input_tokens, output_tokens, status, provider_id) "
            "VALUES ('unknown/model-x', 0, 500, 200, 200, 't')"
        )
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/analytics?days=30")
        assert r.status_code == 200
        assert "pricing" in r.text.lower() or "unpriced" in r.text.lower()
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Filter unpriced models in analytics route**

```python
from janus.storage.usage import get_unpriced_models

raw_unpriced = await get_unpriced_models(db_path, days=days)
registry = request.app.state.pricing_registry
unpriced_models = [m for m in raw_unpriced if registry.get(m["model"]) is None]
```

Pass `unpriced_models` into `dashboard_context`.

- [ ] **Step 4: Update analytics.html**

- Amber banner when `unpriced_models|length > 0` with count + link to `/dashboard/pricing`
- In breakdown table, add badge:

```html
{% if row.cost == 0 and (row.input_tokens + row.output_tokens) > 0 and row.model in unpriced_model_ids %}
<span class="ml-2 text-xs text-amber-400">Unpriced</span>
{% endif %}
```

Pass `unpriced_model_ids` as a set in context.

- [ ] **Step 5: Run test — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add src/janus/dashboard/routes.py src/janus/dashboard/templates/analytics.html \
  tests/integration/test_dashboard.py
git commit -m "feat(dashboard): surface unpriced models in analytics"
```

---

### Task 6: UX polish (toasts, logos, nav, empty states)

**Files:**
- Modify: `src/janus/dashboard/templates/providers.html`
- Modify: `src/janus/dashboard/templates/combos.html`
- Modify: `src/janus/dashboard/templates/leaderboard.html`
- Create: `src/janus/dashboard/static/logos/qwen.svg`
- Create: `src/janus/dashboard/static/logos/opencode.svg`
- Create: `src/janus/dashboard/static/logos/github-copilot.svg`
- Create: `src/janus/dashboard/static/logos/custom.svg`
- Modify: `src/janus/catalog.py` (wire logo filenames)

- [ ] **Step 1: Replace alert() in providers.html and combos.html**

Change:

```javascript
}).catch(err => {
    alert('Failed to update provider: ' + err.message);
});
```

To:

```javascript
}).catch(err => {
    janusToast('Failed to update provider: ' + err.message, 'error');
});
```

Same pattern for combos.

- [ ] **Step 2: Fix leaderboard nav highlight**

Add to `leaderboard.html` after `{% extends "base.html" %}`:

```html
{% block leaderboard_active %}bg-gray-700 text-white{% endblock %}
```

- [ ] **Step 3: Add logo SVGs**

Minimal monochrome SVG icons (~24×24 viewBox) matching existing logo style (white/invert-friendly). Set `logo` field in catalog entries for qwen, opencode, github_copilot, custom.

- [ ] **Step 4: Enhance empty states**

Overview combos section, analytics breakdown, routing no-providers — add blue CTA button links per spec.

- [ ] **Step 5: Manual smoke test**

Run: `janus serve` (or pytest integration suite)
Verify: Providers edit failure shows toast; leaderboard nav highlights; new logos render on Providers catalog gallery.

- [ ] **Step 6: Commit**

```bash
git add src/janus/dashboard/templates/providers.html src/janus/dashboard/templates/combos.html \
  src/janus/dashboard/templates/leaderboard.html src/janus/dashboard/static/logos/ \
  src/janus/catalog.py
git commit -m "fix(dashboard): toasts, logos, nav highlight, empty states"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/unit/dashboard/ tests/integration/test_dashboard.py -v`
Expected: all PASS

- [ ] **Step 2: Run linter**

Run: `ruff check src/janus/dashboard/ src/janus/routing/fallback.py tests/unit/dashboard/`
Expected: no errors

- [ ] **Step 3: Update todo.md**

Mark completed items under "Dashboard & UX" and add note for deferred webhook/CDN bundling.

- [ ] **Step 4: Update CHANGELOG.md `[Unreleased]`**

Add entry: "Dashboard control room — alert system, Overview redesign, routing live state, analytics unpriced warnings, toast notifications."

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| `collect_dashboard_alerts()` | Task 1 |
| Global banner on all pages | Task 2 |
| Overview status strip | Task 3 |
| Attention panel | Task 3 |
| Setup checklist | Task 3 |
| Live snapshot endpoint | Task 3 |
| Routing next-up badge + HTMX poll | Task 4 |
| `routing_snapshot()` | Task 4 |
| Cooldown countdown JS | Task 4 |
| Analytics unpriced banner + badges | Task 5 |
| Toast system | Task 2 + Task 6 |
| Replace alert() | Task 6 |
| Missing logos | Task 6 |
| Leaderboard nav fix | Task 6 |
| Empty state CTAs | Task 6 |

Deferred items (webhook, Copilot equivalent cost, CDN bundling) intentionally have no task.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-dashboard-control-room.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — implement tasks in this session with checkpoints

Which approach do you want?
