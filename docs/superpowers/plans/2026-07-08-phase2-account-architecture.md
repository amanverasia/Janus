# Phase 2 — Account Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Per-model cooldowns, exponential backoff with Retry-After, selectable account strategies, and capability-aware combo routing — all backward-compatible with existing routing tests.

**Architecture:** Extend `cooldowns` table to a compound `(account_id, model)` key with backoff level; make `FallbackHandler` cooldowns model-aware; replace fixed rate-limit cooldowns with exponential backoff in `routing/errors.py`; add strategy selection + capability reordering to `resolve_attempts`; thread `Retry-After` from providers through `_handle`.

**Tech Stack:** Python 3.11+, aiosqlite, Pydantic v2, FastAPI, httpx, pytest, respx, ruff, mypy (strict).

## Global Constraints

- Run tests with `.venv/bin/python -m pytest`, never bare `pytest`.
- `ruff` line-length 100 (E/F/I/N/W/UP); `X | Y` not `Union`; `StrEnum`; `dict[str, Any]` not bare `dict`. `mypy --strict` must pass.
- No code comments unless surrounding code has them.
- `formats/`↔`canonical/`↔`providers/` boundary preserved.
- **Backward-compat is mandatory.** `model=None` ⇒ `"__all__"` and `strategy` defaults to `ROUND_ROBIN`, `required_caps` defaults to empty — so these existing tests pass unchanged: `tests/unit/routing/test_resolver.py`, `test_rate_limit_routing.py`, `test_quota_routing.py`, `test_errors.py`, `tests/integration/test_stream_fallback.py`.
- Reference constants (from 9router source): backoff `base=2000ms · 2^(level-1)`, cap `300s`, `maxLevel=15`; Retry-After cap `1800s`; fixed cooldowns server=30s, auth=300s, network=15s.
- Key facts: `account_id = config.upstream_key_id or config.id`. Cooldown table PK is currently `account_id` only. Migration pattern = `PRAGMA table_info` guard + rebuild.
- Commit after each task with the shown message (end with the Co-Authored-By line).

---

### Task 1: Exponential backoff in `routing/errors.py`

**Files:**
- Modify: `src/janus/routing/errors.py`
- Test: `tests/unit/routing/test_backoff.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `get_cooldown(error_type: str, backoff_level: int = 0) -> tuple[float, int]` returning `(cooldown_seconds, new_backoff_level)`. Module constants `BACKOFF_BASE_MS=2000`, `BACKOFF_MAX_S=300.0`, `BACKOFF_MAX_LEVEL=15`, `RETRY_AFTER_CAP_S=1800.0`, `FIXED_COOLDOWNS`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/routing/test_backoff.py`:

```python
from janus.routing.errors import get_cooldown, RETRY_AFTER_CAP_S


def test_rate_limit_backoff_escalates():
    assert get_cooldown("rate_limit", 0) == (2.0, 1)
    assert get_cooldown("rate_limit", 1) == (4.0, 2)
    assert get_cooldown("rate_limit", 2) == (8.0, 3)


def test_rate_limit_backoff_caps_at_300s():
    secs, level = get_cooldown("rate_limit", 14)
    assert secs == 300.0
    assert level == 15


def test_rate_limit_backoff_level_caps_at_15():
    secs, level = get_cooldown("rate_limit", 99)
    assert level == 15
    assert secs == 300.0


def test_fixed_cooldowns_no_backoff():
    assert get_cooldown("server_error", 0) == (30.0, 0)
    assert get_cooldown("auth_error", 3) == (300.0, 0)
    assert get_cooldown("network", 5) == (15.0, 0)


def test_unknown_error_default():
    assert get_cooldown("unknown", 0) == (60.0, 0)


def test_retry_after_cap_constant():
    assert RETRY_AFTER_CAP_S == 1800.0
```

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/routing/test_backoff.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_cooldown'`.

- [ ] **Step 3: Implement** — append to `src/janus/routing/errors.py`:

```python
BACKOFF_BASE_MS = 2000
BACKOFF_MAX_S = 300.0
BACKOFF_MAX_LEVEL = 15
RETRY_AFTER_CAP_S = 1800.0

FIXED_COOLDOWNS: dict[str, float] = {
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}


def get_cooldown(error_type: str, backoff_level: int = 0) -> tuple[float, int]:
    if error_type == "rate_limit":
        new_level = min(backoff_level + 1, BACKOFF_MAX_LEVEL)
        secs = min(BACKOFF_BASE_MS * (2 ** (new_level - 1)) / 1000, BACKOFF_MAX_S)
        return secs, new_level
    return FIXED_COOLDOWNS.get(error_type, 60.0), 0
```

- [ ] **Step 4: Run — verify pass**
Run: `.venv/bin/python -m pytest tests/unit/routing/test_backoff.py -v && .venv/bin/mypy src/janus/routing/errors.py`
Expected: PASS + mypy clean.

- [ ] **Step 5: Commit**
```bash
git add src/janus/routing/errors.py tests/unit/routing/test_backoff.py
git commit -m "feat(routing): exponential backoff durations (get_cooldown)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Per-model cooldowns storage + migration

**Files:**
- Modify: `src/janus/storage/database.py`, `src/janus/storage/cooldowns.py`
- Test: `tests/unit/storage/test_cooldowns.py` (create; make `tests/unit/storage/__init__.py` if absent)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `cooldowns` table with PK `(account_id, model)`, columns `expires_at REAL`, `error_type TEXT`, `backoff_level INTEGER DEFAULT 0`.
  - `save_cooldown(db_path, account_id, expires_at, model="__all__", error_type=None, backoff_level=0) -> None`.
  - `get_active_cooldowns(db_path) -> dict[str, tuple[float, int]]` keyed `f"{account_id}::{model}"` → `(expires_at, backoff_level)`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/storage/test_cooldowns.py`:

```python
import time

import pytest

from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.database import init_db


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "t.db"
    await init_db(p)
    return p


async def test_save_and_get_per_model(db):
    exp = time.time() + 100
    await save_cooldown(db, "acct-a", exp, model="gpt-4o", error_type="rate_limit", backoff_level=2)
    active = await get_active_cooldowns(db)
    assert "acct-a::gpt-4o" in active
    got_exp, got_level = active["acct-a::gpt-4o"]
    assert abs(got_exp - exp) < 0.01
    assert got_level == 2


async def test_default_model_is_all(db):
    await save_cooldown(db, "acct-b", time.time() + 100)
    active = await get_active_cooldowns(db)
    assert "acct-b::__all__" in active


async def test_expired_pruned(db):
    await save_cooldown(db, "acct-c", time.time() - 5, model="m")
    active = await get_active_cooldowns(db)
    assert "acct-c::m" not in active


async def test_same_account_two_models(db):
    await save_cooldown(db, "acct-d", time.time() + 100, model="m1")
    await save_cooldown(db, "acct-d", time.time() + 100, model="m2")
    active = await get_active_cooldowns(db)
    assert "acct-d::m1" in active and "acct-d::m2" in active
```

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/storage/test_cooldowns.py -v`
Expected: FAIL (old `save_cooldown` has no `model` param / return shape differs).

- [ ] **Step 3a: Migration in `database.py`** — in `_SCHEMA`, replace the `cooldowns` CREATE with:
```sql
CREATE TABLE IF NOT EXISTS cooldowns (
    account_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '__all__',
    expires_at REAL NOT NULL,
    error_type TEXT,
    backoff_level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, model)
);
```
Add migration fn and call it in `init_db` after the existing migrations:
```python
async def _migrate_cooldowns_per_model(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(cooldowns)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if "model" in existing:
        return
    await db.execute(
        """CREATE TABLE cooldowns_new (
            account_id TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '__all__',
            expires_at REAL NOT NULL,
            error_type TEXT,
            backoff_level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, model)
        )"""
    )
    await db.execute(
        "INSERT INTO cooldowns_new (account_id, model, expires_at) "
        "SELECT account_id, '__all__', expires_at FROM cooldowns"
    )
    await db.execute("DROP TABLE cooldowns")
    await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")
```
Call: add `await _migrate_cooldowns_per_model(db)` in `init_db`'s `async with` block (after `_migrate_upstream_key_columns(db)`). (On a fresh DB `_SCHEMA` already creates the new shape and the migration early-returns.)

- [ ] **Step 3b: Rewrite `cooldowns.py`:**
```python
from __future__ import annotations

import time
from pathlib import Path

from .database import get_connection


async def save_cooldown(
    db_path: str | Path,
    account_id: str,
    expires_at: float,
    model: str = "__all__",
    error_type: str | None = None,
    backoff_level: int = 0,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at, error_type, backoff_level) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, model) DO UPDATE SET "
            "expires_at = excluded.expires_at, error_type = excluded.error_type, "
            "backoff_level = excluded.backoff_level",
            (account_id, model, expires_at, error_type, backoff_level),
        )
        await db.commit()


async def get_active_cooldowns(db_path: str | Path) -> dict[str, tuple[float, int]]:
    now = time.time()
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM cooldowns WHERE expires_at <= ?", (now,))
        await db.commit()
        async with db.execute(
            "SELECT account_id, model, expires_at, backoff_level FROM cooldowns"
        ) as cur:
            rows = await cur.fetchall()
    return {
        f"{row['account_id']}::{row['model']}": (row["expires_at"], row["backoff_level"])
        for row in rows
    }
```

- [ ] **Step 4: Run — verify pass**
Run: `.venv/bin/python -m pytest tests/unit/storage/test_cooldowns.py -v && .venv/bin/mypy src/janus/storage/cooldowns.py src/janus/storage/database.py`
Expected: PASS + mypy clean. (`FallbackHandler.load_cooldowns` will break at type level — that's Task 3; if mypy flags fallback.py here, ignore, it's fixed next task. Run mypy only on the two files listed.)

- [ ] **Step 5: Commit**
```bash
git add src/janus/storage/cooldowns.py src/janus/storage/database.py tests/unit/storage/
git commit -m "feat(storage): per-model cooldowns table (account_id, model) + backoff_level

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: FallbackHandler — model-aware cooldowns + backoff + mark_success

**Files:**
- Modify: `src/janus/routing/fallback.py`
- Test: `tests/unit/routing/test_cooldowns_per_model.py` (create), and keep `test_resolver.py` green.

**Interfaces:**
- Consumes: `get_cooldown` (Task 1), `save_cooldown`/`get_active_cooldowns` new shapes (Task 2).
- Produces:
  - `is_available(account_id, model=None) -> bool` — checks `(account_id, model)` and `(account_id, "__all__")`.
  - `mark_cooldown(account_id, error_type, model=None, retry_after=None, duration=None) -> None` — exponential backoff via level tracked per `(account_id, model_key)`; `retry_after` capped at `RETRY_AFTER_CAP_S`, resets level to 0; `duration` overrides outright.
  - `mark_success(account_id, model=None) -> None` — clears `(account_id, model_key)` + `(account_id, "__all__")` in-memory + best-effort DB delete; resets backoff level.
  - `resolve_attempts` filters availability using the request's specific model.

Internal state changes: `self._cooldowns: dict[tuple[str, str], float]`, `self._backoff: dict[tuple[str, str], int]`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/routing/test_cooldowns_per_model.py`:

```python
from janus.routing.fallback import FallbackHandler
from janus.providers.registry import ProviderRegistry


def _handler():
    return FallbackHandler(ProviderRegistry(), db_path=None)


def test_model_cooldown_does_not_block_other_model():
    h = _handler()
    h.mark_cooldown("acct-a", "rate_limit", model="gpt-4o")
    assert not h.is_available("acct-a", "gpt-4o")
    assert h.is_available("acct-a", "gpt-4o-mini")


def test_all_cooldown_blocks_every_model():
    h = _handler()
    h.mark_cooldown("acct-a", "auth_error")  # model=None -> __all__
    assert not h.is_available("acct-a", "gpt-4o")
    assert not h.is_available("acct-a", "anything")
    assert not h.is_available("acct-a")


def test_backoff_escalates_then_success_resets():
    h = _handler()
    h.mark_cooldown("acct-b", "rate_limit", model="m")
    first = h._cooldowns[("acct-b", "m")]
    h.mark_success("acct-b", "m")
    assert h.is_available("acct-b", "m")
    assert ("acct-b", "m") not in h._backoff or h._backoff[("acct-b", "m")] == 0


def test_retry_after_overrides_backoff():
    import time
    h = _handler()
    h.mark_cooldown("acct-c", "rate_limit", model="m", retry_after=120.0)
    remaining = h._cooldowns[("acct-c", "m")] - time.time()
    assert 100 < remaining <= 120
```

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/routing/test_cooldowns_per_model.py -v`
Expected: FAIL (is_available takes 1 arg; keys are str not tuple).

- [ ] **Step 3: Implement in `fallback.py`.** Replace cooldown internals:
  - Constructor: `self._cooldowns: dict[tuple[str, str], float] = {}` and `self._backoff: dict[tuple[str, str], int] = {}`.
  - Import `get_cooldown, RETRY_AFTER_CAP_S` from `janus.routing.errors`; keep `save_cooldown` import (new signature).
  - `is_available`:
```python
def is_available(self, account_id: str, model: str | None = None) -> bool:
    now = time.time()
    all_exp = self._cooldowns.get((account_id, "__all__"))
    if all_exp is not None and now < all_exp:
        return False
    if model is not None:
        exp = self._cooldowns.get((account_id, model))
        if exp is not None and now < exp:
            return False
    return True
```
  - `mark_cooldown`:
```python
def mark_cooldown(self, account_id, error_type, model=None, retry_after=None, duration=None):
    model_key = model or "__all__"
    key = (account_id, model_key)
    if duration is not None:
        cooldown, level = duration, self._backoff.get(key, 0)
    elif retry_after is not None:
        cooldown, level = min(retry_after, RETRY_AFTER_CAP_S), 0
    else:
        cooldown, level = get_cooldown(error_type, self._backoff.get(key, 0))
    self._backoff[key] = level
    expires_at = time.time() + cooldown
    self._cooldowns[key] = expires_at
    if self.db_path is not None:
        self._persist_cooldown(account_id, model_key, expires_at, error_type, level)
```
  - `mark_success`:
```python
def mark_success(self, account_id: str, model: str | None = None) -> None:
    for mk in {model or "__all__", "__all__"}:
        self._cooldowns.pop((account_id, mk), None)
        self._backoff.pop((account_id, mk), None)
        if self.db_path is not None:
            self._delete_cooldown(account_id, mk)
```
  - `_persist_cooldown(account_id, model, expires_at, error_type, level)`: schedule `save_cooldown(self.db_path, account_id, expires_at, model=model, error_type=error_type, backoff_level=level)` (same loop.create_task guard as today).
  - Add `_delete_cooldown(account_id, model)`: schedule a small coroutine deleting that row (add `delete_cooldown(db_path, account_id, model)` to `cooldowns.py` — a 4-line `DELETE ... WHERE account_id=? AND model=?`). Guard with the same running-loop try/except.
  - `load_cooldowns`: adapt to the new return shape:
```python
async def load_cooldowns(self) -> None:
    if self.db_path is None:
        return
    active = await get_active_cooldowns(self.db_path)
    for combined, (expires_at, level) in active.items():
        account_id, _, model = combined.partition("::")
        self._cooldowns[(account_id, model)] = expires_at
        if level:
            self._backoff[(account_id, model)] = level
```
  - `resolve_attempts`: compute `specific_model` and pass to `is_available`:
    - single-model: `prefix, _, specific = model_str.partition("/")` then `available = [t for t in targets if self.is_available(t.account_id, specific)]`.
    - combo: for each `m`, `_, _, specific = m.partition("/")`, filter with `is_available(t.account_id, specific)`.

- [ ] **Step 4: Run — verify per-model tests pass AND resolver tests still green**
Run: `.venv/bin/python -m pytest tests/unit/routing/ -v`
Expected: all PASS. If a `test_resolver.py` case that calls `mark_cooldown("x","rate_limit")` then `is_available("x")` fails, confirm `is_available("x")` (model=None) reads `__all__` — it should still be False. Fix implementation, not the test.

- [ ] **Step 5: mypy + commit**
Run: `.venv/bin/mypy src/janus/routing/ src/janus/storage/cooldowns.py`
```bash
git add src/janus/routing/fallback.py src/janus/storage/cooldowns.py tests/unit/routing/test_cooldowns_per_model.py
git commit -m "feat(routing): model-aware cooldowns, exponential backoff, mark_success

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Retry-After on RawResult + provider parsing

**Files:**
- Modify: `src/janus/providers/base.py`, `openai_compat.py`, `anthropic.py`, `gemini.py`, `github_copilot.py`
- Test: `tests/unit/providers/test_retry_after.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `RawResult.retry_after: float | None = None`. Providers set it from the `Retry-After` response header on ≥400 responses (both streaming pre-check and non-streaming). Helper `parse_retry_after(headers) -> float | None` in `base.py` (integer-seconds form; ignore HTTP-date form for now, returning None).

- [ ] **Step 1: Write the failing test** — `tests/unit/providers/test_retry_after.py`:
```python
import httpx
import respx

from janus.providers.base import parse_retry_after
from janus.providers.openai_compat import OpenAICompatProvider


def test_parse_retry_after_seconds():
    assert parse_retry_after({"retry-after": "42"}) == 42.0


def test_parse_retry_after_absent():
    assert parse_retry_after({}) is None


def test_parse_retry_after_nonnumeric_returns_none():
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}) is None


@respx.mock
async def test_stream_429_sets_retry_after():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"}, json={"e": 1})
    )
    p = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    r = await p.call({"model": "m", "messages": []}, stream=True)
    assert r.status_code == 429
    assert r.retry_after == 30.0
    await p.close()
```

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/providers/test_retry_after.py -v`
Expected: FAIL — no `parse_retry_after`; `retry_after` attr missing.

- [ ] **Step 3: Implement.**
  - `base.py`: add `retry_after: float | None = None` to `RawResult`; add:
```python
def parse_retry_after(headers: Any) -> float | None:
    try:
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
```
  (Import `Any` already present.)
  - In each provider's `_call_stream`, on the `r.status_code >= 400` branch, build the error result with `retry_after=parse_retry_after(r.headers)`. Also in the non-stream `call` path, when returning a ≥400 `RawResult`, set `retry_after=parse_retry_after(r.headers)` (openai_compat/anthropic/gemini/github_copilot `call`).
  - Import `parse_retry_after` from `.base` in each provider.

- [ ] **Step 4: Run — verify pass**
Run: `.venv/bin/python -m pytest tests/unit/providers/ -v && .venv/bin/mypy src/janus/providers/`
Expected: PASS + mypy clean.

- [ ] **Step 5: Commit**
```bash
git add src/janus/providers/ tests/unit/providers/test_retry_after.py
git commit -m "feat(providers): parse Retry-After into RawResult.retry_after

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Wire backoff/Retry-After/mark_success into `_handle`

**Files:**
- Modify: `src/janus/api/routes.py`
- Test: `tests/integration/test_backoff_escalation.py` (create)

**Interfaces:**
- Consumes: `mark_cooldown(retry_after=)`, `mark_success` (Task 3); `RawResult.retry_after` (Task 4).
- Produces: `_handle` cools down the specific model, passes `retry_after`, and calls `mark_success` on 2xx.

Notes: `specific_model = canonical_req.model.split("/", 1)[1] if "/" in canonical_req.model else canonical_req.model`. On every `mark_cooldown` call in `_handle` (streaming 4xx, non-streaming 4xx, network), add `model=specific_model` and, where a `result` exists, `retry_after=result.retry_after`. On success (non-streaming after 2xx return; streaming inside the generator's `finally` only when no error occurred) call `handler.mark_success(target.account_id, specific_model)`.

- [ ] **Step 1: Write the failing integration test** — `tests/integration/test_backoff_escalation.py`. Mirror the fixture in `tests/integration/test_stream_fallback.py` (two accounts). Assert: two sequential non-streaming requests where account A returns 429 both times produce an escalating cooldown on `(A, model)` (inspect `app.state.fallback_handler._backoff[("<A account_id>", "m1")] >= 2` after the second), and account B (200) serves both. Use `respx` with a 429 on A and a valid JSON completion on B. Resolve A's account_id via `app.state.registry.lookup("test/m1")`.

(Full fixture: copy `two_account_app` from `test_stream_fallback.py` verbatim — same provider config — into this file.)

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/integration/test_backoff_escalation.py -v`
Expected: FAIL (backoff not wired; `_backoff` never escalates because `_handle` passes no model).

- [ ] **Step 3: Implement in `_handle`.** Add `specific_model` after `canonical_req` is finalized. Update the three `mark_cooldown` sites:
  - streaming ≥400 eligible: `handler.mark_cooldown(target.account_id, classify_error(result.status_code).value, model=specific_model, retry_after=result.retry_after)`
  - non-streaming ≥400 eligible: same with its `result`.
  - network except: `handler.mark_cooldown(target.account_id, "network", model=specific_model)`
  Add success calls:
  - non-streaming: right before `return JSONResponse(...)`, add `handler.mark_success(target.account_id, specific_model)`.
  - streaming: in `_streaming_generator`'s `finally`, after usage recording, add `handler.mark_success(target.account_id, specific_model)` (stream reaching finally without upstream error = success).

- [ ] **Step 4: Run — verify pass + no regressions**
Run: `.venv/bin/python -m pytest tests/integration/ tests/unit/routing/ -v`
Expected: PASS incl. `test_stream_fallback.py`.

- [ ] **Step 5: mypy + commit**
```bash
git add src/janus/api/routes.py tests/integration/test_backoff_escalation.py
git commit -m "feat(routing): wire per-model cooldown, Retry-After, mark_success into _handle

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Account strategies (fill-first / round-robin / sticky-N)

**Files:**
- Modify: `src/janus/routing/fallback.py`, `src/janus/storage/settings.py`, `src/janus/api/routes.py`
- Test: `tests/unit/routing/test_account_strategies.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `AccountStrategy` StrEnum in `fallback.py`: `FILL_FIRST="fill_first"`, `ROUND_ROBIN="round_robin"`, `STICKY_RR="sticky_rr"`.
  - `resolve_attempts(..., strategy: AccountStrategy = AccountStrategy.ROUND_ROBIN, sticky_limit: int = 3)`.
  - settings `server_account_strategy="round_robin"`, `server_sticky_limit="3"` + `resolve_account_strategy(settings)->str`, `resolve_sticky_limit(settings)->int`.

- [ ] **Step 1: Write the failing test** — `tests/unit/routing/test_account_strategies.py`. Build a registry with 3 accounts under one prefix (reuse the `_config`/registry helper pattern from `test_rate_limit_routing.py` — read it first). Assert:
  - `FILL_FIRST`: repeated `resolve_attempts` always returns the same first account (no rotation).
  - `ROUND_ROBIN`: first account advances each call (current behavior).
  - `STICKY_RR` with `sticky_limit=2`: same first account for 2 calls, then advances.

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/routing/test_account_strategies.py -v`
Expected: FAIL (no `strategy` kwarg / `AccountStrategy`).

- [ ] **Step 3: Implement.**
  - Add `AccountStrategy` StrEnum at top of `fallback.py`.
  - Add `self._sticky: dict[str, tuple[str, int]] = {}` (pool_key → (account_id, count)) to constructor.
  - New `_order_by_strategy(pool_key, accounts, strategy, sticky_limit)`:
    - `FILL_FIRST`: return `accounts` unchanged.
    - `ROUND_ROBIN`: existing `_rotate_accounts` logic (counter-based).
    - `STICKY_RR`: if `len<=1` return as-is; look at `self._sticky.get(pool_key)`; if head account still present and count < limit → keep same head order, increment count; else advance rotation index (reuse `_rotation_counters`), set sticky to `(new_head, 1)`. Return rotated list.
  - `resolve_attempts` accepts `strategy`/`sticky_limit`, calls `_order_by_strategy` instead of `_rotate_accounts` directly (keep client-key sticky override precedence exactly as today: if `sticky_client_key and client_key_id is not None`, use the deterministic client-key path regardless of strategy).
  - `settings.py`: add the two defaults + resolve helpers (mirror `sticky_client_key_routing_enabled`).
  - `routes.py`: read `strategy = AccountStrategy(resolve_account_strategy(settings))` and `sticky_limit = resolve_sticky_limit(settings)`, pass to `resolve_attempts`. Wrap the enum call so an unknown value falls back to `ROUND_ROBIN`.

- [ ] **Step 4: Run — verify pass + resolver regressions**
Run: `.venv/bin/python -m pytest tests/unit/routing/ tests/integration/ -v`
Expected: all PASS (default `ROUND_ROBIN` preserves `test_resolver.py`).

- [ ] **Step 5: mypy + commit**
```bash
git add src/janus/routing/fallback.py src/janus/storage/settings.py src/janus/api/routes.py tests/unit/routing/test_account_strategies.py
git commit -m "feat(routing): fill_first/round_robin/sticky_rr account strategies

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Capability-aware combo routing

**Files:**
- Create: `src/janus/routing/capabilities.py`
- Modify: `src/janus/catalog.py` (add `capabilities` to a few gateway entries), `src/janus/routing/fallback.py`, `src/janus/api/routes.py`
- Test: `tests/unit/routing/test_capabilities.py` (create)

**Interfaces:**
- Consumes: `CanonicalRequest`, `ImagePart`.
- Produces:
  - `detect_required_capabilities(req: CanonicalRequest) -> frozenset[str]`.
  - `reorder_combo_by_capabilities(models: list[str], required: frozenset[str]) -> list[str]`.
  - `get_provider_capabilities(prefix: str) -> dict[str, bool]` (reads `catalog.PROVIDERS[...]["gateway"]["capabilities"]`, default `{"tool_use": True}`).
  - `resolve_attempts(..., required_caps: frozenset[str] = frozenset())` reorders combo models when non-empty.

- [ ] **Step 1: Write the failing test** — `tests/unit/routing/test_capabilities.py`:
```python
from janus.canonical.models import CanonicalRequest, ImagePart, ImageSource, Message, Role, TextPart
from janus.routing.capabilities import (
    detect_required_capabilities,
    reorder_combo_by_capabilities,
)


def test_detect_vision_from_image_part():
    req = CanonicalRequest(
        model="c",
        messages=[Message(role=Role.USER, content=[ImagePart(source=ImageSource(type="url", url="x"))])],
    )
    assert "vision" in detect_required_capabilities(req)


def test_detect_none_for_text():
    req = CanonicalRequest(model="c", messages=[Message(role=Role.USER, content=[TextPart(text="hi")])])
    assert detect_required_capabilities(req) == frozenset()


def test_reorder_prioritizes_vision_capable(monkeypatch):
    import janus.routing.capabilities as cap
    caps = {"openai": {"vision": True, "tool_use": True}, "groq": {"vision": False, "tool_use": True}}
    monkeypatch.setattr(cap, "get_provider_capabilities", lambda p: caps.get(p, {"tool_use": True}))
    models = ["groq/x", "openai/y"]
    out = reorder_combo_by_capabilities(models, frozenset({"vision"}))
    assert out[0] == "openai/y"
    assert set(out) == set(models)  # nothing dropped


def test_reorder_noop_without_required():
    models = ["groq/x", "openai/y"]
    assert reorder_combo_by_capabilities(models, frozenset()) == models
```

- [ ] **Step 2: Run — verify fail**
Run: `.venv/bin/python -m pytest tests/unit/routing/test_capabilities.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement.**
  - `capabilities.py`:
```python
from __future__ import annotations

from janus.canonical.models import CanonicalRequest, ImagePart, Role

HARD_CAPS = frozenset({"vision", "pdf"})


def get_provider_capabilities(prefix: str) -> dict[str, bool]:
    from janus.catalog import PROVIDERS, gateway_entries
    entries = gateway_entries()
    entry = entries.get(prefix, {})
    caps = entry.get("capabilities")
    if isinstance(caps, dict):
        return caps
    return {"tool_use": True}


def detect_required_capabilities(req: CanonicalRequest) -> frozenset[str]:
    required: set[str] = set()
    for msg in reversed(req.messages):
        if msg.role != Role.USER:
            continue
        if isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, ImagePart):
                    required.add("vision")
        break
    for tool in req.tools:
        if "search" in tool.function.name.lower():
            required.add("search")
    return frozenset(required)


def reorder_combo_by_capabilities(
    models: list[str], required: frozenset[str]
) -> list[str]:
    if not required or len(models) <= 1:
        return models
    hard = required & HARD_CAPS

    def tier(model_str: str) -> int:
        prefix = model_str.split("/", 1)[0] if "/" in model_str else model_str
        caps = get_provider_capabilities(prefix)
        if not all(caps.get(c) for c in hard):
            return 2
        if all(caps.get(c) for c in required):
            return 0
        return 1

    return [m for _, m in sorted(enumerate(models), key=lambda im: (tier(im[1]), im[0]))]
```
  - `catalog.py`: add a `"capabilities": {"vision": True, "pdf": True, "tool_use": True}` (or `vision: False`) to the `gateway` block of at least openai, anthropic, gemini, groq (groq vision False). Keep it minimal — additive.
  - `fallback.py`: `resolve_attempts(..., required_caps=frozenset())`; in the combo branch, `combo_models = reorder_combo_by_capabilities(combo_models, required_caps)` before the loop.
  - `routes.py`: `required_caps = detect_required_capabilities(canonical_req)` and pass to `resolve_attempts`.

- [ ] **Step 4: Run — verify pass + regressions**
Run: `.venv/bin/python -m pytest tests/unit/routing/ tests/integration/ -v && .venv/bin/mypy src/janus/routing/ src/janus/api/routes.py`
Expected: all PASS + mypy clean.

- [ ] **Step 5: Commit**
```bash
git add src/janus/routing/capabilities.py src/janus/catalog.py src/janus/routing/fallback.py src/janus/api/routes.py tests/unit/routing/test_capabilities.py
git commit -m "feat(routing): capability-aware combo reordering (vision/pdf/search)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Per-model cooldown end-to-end integration + full gate

**Files:**
- Test: `tests/integration/test_per_model_cooldown_e2e.py` (create)

- [ ] **Step 1: Write the test.** Reuse `two_account_app` (copy from `test_stream_fallback.py`) but give one account two models `["m1", "m2"]`. Mock account A's endpoint to 429 only — then send a request for `test/m1`; assert it rotates to B. Then assert (via `app.state.fallback_handler.is_available`) that A is still available for `m2` (per-model cooldown) but not for `m1`.

- [ ] **Step 2: Run — verify pass**
Run: `.venv/bin/python -m pytest tests/integration/test_per_model_cooldown_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Full regression gate**
Run: `.venv/bin/python -m pytest -q`
Expected: all green.
Run: `.venv/bin/ruff check src/janus/ tests/ && .venv/bin/ruff format --check src/janus/ tests/ && .venv/bin/mypy src/janus/`
Expected: all clean. Fix inline and re-run.

- [ ] **Step 4: Commit**
```bash
git add tests/integration/test_per_model_cooldown_e2e.py
git commit -m "test(integration): per-model cooldown isolation end-to-end

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** A (per-model cooldowns) → Tasks 2,3,8. B (backoff+Retry-After) → Tasks 1,3,4,5. C (strategies) → Task 6. D (capabilities) → Task 7. All spec sections mapped.

**Placeholder scan:** No TBD. Tasks 5/8 reference "copy the `two_account_app` fixture from test_stream_fallback.py" rather than duplicating ~40 lines — the source is stable and named exactly; acceptable.

**Type consistency:** `get_cooldown -> tuple[float,int]`, `get_active_cooldowns -> dict[str, tuple[float,int]]`, `is_available(account_id, model=None)`, `mark_cooldown(account_id, error_type, model=None, retry_after=None, duration=None)`, `mark_success(account_id, model=None)`, `AccountStrategy` values, `resolve_attempts` new kwargs — all consistent across tasks.

**Ordering/deps:** 1→3 (backoff), 2→3 (storage), 3→5 (handler wiring), 4→5 (retry_after), 6 and 7 independent of 5 but share `resolve_attempts`/`routes.py` (sequential to avoid conflicts). 8 depends on 3,5. Linear execution 1..8 is safe.

**Regression guard:** every task step 4 runs `tests/unit/routing/` and/or `tests/integration/`; defaults chosen so `test_resolver.py` etc. pass unchanged.
