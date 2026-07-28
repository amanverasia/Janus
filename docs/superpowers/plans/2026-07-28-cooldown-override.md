# Cooldown Override Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent `server_cooldowns_enabled` toggle (default on) plus a Clear-all-cooldowns action, then ship as Janus **v2.0.0**.

**Architecture:** `FallbackHandler` gains `cooldowns_enabled` (hot-updated from settings). When false, `is_available` ignores cooldown timers and `mark_cooldown` no-ops. `clear_all_cooldowns` wipes memory + SQLite. Dashboard Settings + Routing + CLI expose the controls.

**Tech Stack:** Python 3.11, aiosqlite settings/cooldowns, FastAPI/HTMX dashboard, pytest

**Spec:** [`docs/superpowers/specs/2026-07-28-cooldown-override-design.md`](../specs/2026-07-28-cooldown-override-design.md)

## Global Constraints

- Default remains cooldowns **on** (`server_cooldowns_enabled=true`)
- Do not change cooldown duration / backoff formulas
- Do not disable inventory RPM deprioritization or subscription quota soft deprioritization
- Use `.venv/bin/python -m pytest` for tests
- Version **2.0.0** cut after merge (release commit + tag), not mid-feature unless noted

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/janus/routing/fallback.py` | `cooldowns_enabled`, no-op mark, ignore in `is_available`, `clear_all_cooldowns()`, adopt state |
| `src/janus/storage/cooldowns.py` | `clear_all_cooldowns(db_path)` |
| `src/janus/storage/settings.py` | Default `server_cooldowns_enabled: "true"` |
| `src/janus/dashboard/routes.py` | Apply flag on settings save; clear endpoint; routing context flag |
| `src/janus/dashboard/alerts.py` | Suppress/replace cooldown warnings when disabled |
| `src/janus/dashboard/templates/settings.html` | Toggle UI |
| `src/janus/dashboard/templates/routing.html` | Disabled banner + Clear button |
| `src/janus/cli.py` | Allowlist setting key |
| `src/janus/app.py` / `reload.py` | Seed handler flag from settings on startup/reload |
| `docs/` + `CHANGELOG.md` | Operator docs; Unreleased → 2.0.0 on release |
| Tests | Unit fallback + storage; light dashboard/settings coverage |

---

### Task 1: FallbackHandler enable flag + clear

**Files:**
- Modify: `src/janus/routing/fallback.py`
- Modify: `src/janus/storage/cooldowns.py`
- Create/modify: `tests/unit/routing/test_cooldown_override.py`

**Interfaces:**
- `FallbackHandler.cooldowns_enabled: bool` (default `True`)
- `mark_cooldown` no-op when disabled
- `is_available` returns `True` for cooldown checks when disabled
- `async def clear_all_cooldowns(self) -> int` — clears `_cooldowns` + `_backoff`, calls storage clear, returns count cleared
- `async def clear_all_cooldowns(db_path) -> int` in storage
- `adopt_runtime_state` copies `cooldowns_enabled`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/routing/test_cooldown_override.py
import pytest
from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler


def _handler():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="a",
            prefix="p",
            api_type="openai_compat",
            base_url="https://x.com",
            api_key="k",
            models=["m"],
        )
    )
    return FallbackHandler(registry)


def test_disabled_ignores_existing_cooldown():
    h = _handler()
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    assert h.is_available("a") is False
    h.cooldowns_enabled = False
    assert h.is_available("a") is True
    attempts = h.resolve_attempts("p/m")
    assert len(attempts) == 1


def test_disabled_mark_cooldown_noop():
    h = _handler()
    h.cooldowns_enabled = False
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    assert h._cooldowns == {}
    h.cooldowns_enabled = True
    assert h.is_available("a") is True


@pytest.mark.asyncio
async def test_clear_all_empties_memory(tmp_path):
    from janus.storage.database import init_db
    from janus.storage.cooldowns import get_active_cooldowns, save_cooldown

    db = tmp_path / "j.db"
    await init_db(db)
    h = _handler()
    h.db_path = db
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    await save_cooldown(db, "b", expires_at=__import__("time").time() + 60)
    n = await h.clear_all_cooldowns()
    assert n >= 1
    assert h._cooldowns == {}
    assert await get_active_cooldowns(db) == {}
```

- [ ] **Step 2: Run tests — expect FAIL**

`.venv/bin/python -m pytest tests/unit/routing/test_cooldown_override.py -v`

- [ ] **Step 3: Implement storage + handler**

```python
# storage/cooldowns.py
async def clear_all_cooldowns(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute("DELETE FROM cooldowns")
        await db.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
```

In `FallbackHandler.__init__`: `self.cooldowns_enabled = True`

In `is_available`: if not `self.cooldowns_enabled`: return True

In `mark_cooldown`: if not `self.cooldowns_enabled`: return

```python
async def clear_all_cooldowns(self) -> int:
    count = len(self._cooldowns)
    self._cooldowns.clear()
    self._backoff.clear()
    if self.db_path is not None:
        from janus.storage.cooldowns import clear_all_cooldowns as clear_db
        db_count = await clear_db(self.db_path)
        return max(count, db_count)
    return count
```

Copy `cooldowns_enabled` in `adopt_runtime_state`.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit** `feat: add cooldown disable flag and clear-all on FallbackHandler`

---

### Task 2: Settings default, startup/reload, CLI, settings save hot-apply

**Files:**
- Modify: `src/janus/storage/settings.py`
- Modify: `src/janus/app.py` (after FallbackHandler create / load_cooldowns)
- Modify: `src/janus/dashboard/reload.py`
- Modify: `src/janus/dashboard/routes.py` (`api_update_setting`)
- Modify: `src/janus/cli.py` (SETTINGS keys list)
- Test: extend unit tests or `tests/unit/storage/test_settings.py` if present

- [ ] **Step 1: Add default** `"server_cooldowns_enabled": "true"` to `SERVER_SETTING_DEFAULTS`

- [ ] **Step 2: Helper**

```python
def cooldowns_enabled_from_settings(settings: dict[str, str]) -> bool:
    return settings.get("server_cooldowns_enabled", "true").lower() != "false"
```

(Place in `settings.py` or small helper used by app/routes.)

- [ ] **Step 3: On startup** after creating `FallbackHandler`, set flag from `get_all_settings` / `get_setting`

- [ ] **Step 4: On `api_update_setting`** when key is `server_cooldowns_enabled`, update `request.app.state.fallback_handler.cooldowns_enabled`

- [ ] **Step 5: After `reload_providers` rebuilds handler**, re-read setting onto new handler (or adopt from old including flag then overwrite from DB)

- [ ] **Step 6: CLI** add `"server_cooldowns_enabled"` to allowlisted keys

- [ ] **Step 7: Commit** `feat: wire server_cooldowns_enabled setting to FallbackHandler`

---

### Task 3: Dashboard UI + clear endpoint + alerts

**Files:**
- Modify: `settings.html`, `routing.html`
- Modify: `routes.py` (routing page context, clear POST)
- Modify: `alerts.py`
- Test: dashboard route test if easy pattern exists

- [ ] **Step 1: Settings checkbox** — mirror `server_require_api_key` toggle pattern for `server_cooldowns_enabled` (label: “Enable account cooldowns”)

- [ ] **Step 2: Routing** — if disabled, blue/amber info banner; always show **Clear all cooldowns** button:

```html
<button hx-post="/dashboard/api/routing/cooldowns/clear"
        hx-target="#cooldown-clear-status"
        class="...">Clear all cooldowns</button>
<span id="cooldown-clear-status"></span>
```

- [ ] **Step 3: Endpoint**

```python
@router.post("/api/routing/cooldowns/clear", response_class=HTMLResponse)
async def api_clear_cooldowns(request: Request) -> HTMLResponse:
    handler = request.app.state.fallback_handler
    n = await handler.clear_all_cooldowns()
    return HTMLResponse(f'<span class="text-green-400">Cleared {n} cooldown(s)</span>')
```

- [ ] **Step 4: Alerts** — if setting false, skip `_cooldown_alerts` warning (or emit single info “Cooldowns disabled”)

- [ ] **Step 5: Commit** `feat: dashboard cooldown toggle, clear action, and alert handling`

---

### Task 4: Docs + changelog stub + regression

**Files:**
- Modify: relevant docs (`docs/configuration.md` or routing docs — find best existing page)
- Modify: `CHANGELOG.md` under Unreleased for 2.0.0 bullets
- Run focused + fallback/resolver tests

- [ ] **Step 1: Docs** — document setting + clear button

- [ ] **Step 2: CHANGELOG Unreleased** noting v2.0.0 intent (toggle + clear)

- [ ] **Step 3: Pytest** routing/fallback/settings/catalog unrelated green

- [ ] **Step 4: Commit** `docs: document cooldown override controls`

---

### Task 5: PR, merge, release v2.0.0

- [ ] Push branch, open PR, wait for CI green, merge
- [ ] On main: bump `pyproject.toml` + `app.py` to `2.0.0`, move changelog to `## [2.0.0] - 2026-07-28`
- [ ] Tag `v2.0.0`, push, `gh release create`

---

## Spec coverage

| Requirement | Task |
|-------------|------|
| `server_cooldowns_enabled` default true | 2 |
| Disable: ignore + no-op mark | 1 |
| Clear all memory+DB | 1, 3 |
| Settings + CLI | 2, 3 |
| Routing UI + banner | 3 |
| Alerts when disabled | 3 |
| Hot-apply | 2 |
| Docs + v2.0.0 | 4, 5 |
