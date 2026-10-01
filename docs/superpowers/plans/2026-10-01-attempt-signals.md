# Per-attempt Latency and Reliability Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record TTFT, output tokens/sec, and outcome for every upstream attempt in a new
`attempt_signals` table, and expose a cached per-model / per-account aggregate.

**Architecture:** A storage module owns the table, insert, and throttled prune. A request-path
glue object (`AttemptSignal`, in `api/signals.py`) is created per upstream attempt, hung on the
existing `_OutcomeRecorder`, and finished exactly once (ok / error / client_error / aborted) from
the failure hook, `_log_error_and_raise`, success paths, and stream `finally` blocks. Writes run
in tracked background tasks drained on shutdown. An aggregation module computes percentiles with
a 60 s cache invalidated on provider reload.

**Tech Stack:** Python 3.11, aiosqlite, FastAPI, pytest-asyncio (auto mode), respx.

**Spec:** `docs/superpowers/specs/2026-10-01-attempt-signals-design.md`

## Global Constraints

- Run tools as `.venv/bin/python -m <tool>` from the worktree root
  (`/home/amanverasia/Projects/personal_projects/development/Janus-wt-187-signals`). If the
  worktree has no `.venv`, create one: `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- `formats/` and `providers/` are not modified.
- ruff line-length 100 (E, F, I, N, W, UP); `mypy --strict`; `X | Y` unions; no code comments.
- Signal recording/aggregation is fail-safe: exceptions are logged at warning and swallowed.
- Outcome values are exactly `ok`, `error`, `client_error`, `aborted`.
- TPS floor: `MIN_TPS_WINDOW_S = 0.05` (50 ms). Retention: `ATTEMPT_SIGNAL_RETENTION_DAYS = 7`,
  pruned at most once per hour per DB path. Aggregate: 7-day window, `max_rows = 50_000`,
  60 s cache, `sufficient` when `samples >= 5`.
- No routing or dashboard behavior change in this plan.
- Commit after every task; message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Never patch `time.monotonic` on the shared `time` module in tests: storage code (prune
throttle) and the event loop also call it. Patch the module-level `time` name of the module
under test with a `types.SimpleNamespace(monotonic=...)` instead.

## Review Focus

1. A request that is served from the prompt cache makes no upstream attempt → no signal row.
   (Covered by Task 4 test `test_prompt_cache_hit_records_no_signal`.)
2. `_passthrough_call` returns `None` (provider missing) and the loop `continue`s without a
   failure note → the unfinished signal must not be written as a phantom row, and must not be
   attributed to the exhausted 503. (Task 4 resets `outcome.attempt_signal = None` at loop top;
   Task 3 test `test_unfinished_signal_writes_nothing`.)
3. An unexpected exception inside an attempt (not `httpx.RequestError`) escapes to `_handle`'s
   catch-all → the in-flight attempt is recorded once as `error` / 500. (Task 4
   `test_unexpected_exception_records_error_signal`.)
4. A double finish (e.g. stream `finally` after an earlier finish) never writes two rows.
   (Task 3 `test_finish_is_idempotent`.)
5. A streaming client abort's `finally` runs inside a cancelled scope → the write must still
   land (scheduled task, not awaited). (Task 4 `test_client_abort_records_aborted_signal`.)

---

### Task 1: `attempt_signals` table, insert, and throttled prune

**Files:**
- Modify: `src/janus/storage/database.py` (schema string near `request_outcomes`, ~line 144-164)
- Create: `src/janus/storage/attempt_signals.py`
- Test: `tests/unit/storage/test_attempt_signals.py`

**Interfaces:**
- Produces:
  - `ATTEMPT_SIGNAL_RETENTION_DAYS: int = 7`
  - `async def record_attempt_signal(db_path: str | Path, *, model: str, account_id: str, provider_id: str | None, client_format: str | None, streamed: bool, outcome: str, status: int | None, ttft_ms: int | None, duration_ms: int, output_tokens: int, output_tps: float | None) -> None`
  - `async def list_attempt_signals(db_path: str | Path, *, limit: int = 100) -> list[dict[str, Any]]` (newest first, all columns)
  - `async def prune_attempt_signals(db_path: str | Path, retention_days: int = ATTEMPT_SIGNAL_RETENTION_DAYS) -> int`
  - `def reset_attempt_signal_prune_throttle() -> None` (test helper; clears the throttle dict)

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from janus.storage import attempt_signals as sig
from janus.storage.database import get_connection, init_db


def _row(**overrides):
    base = dict(
        model="m1",
        account_id="acct-a",
        provider_id="acct-a",
        client_format="openai",
        streamed=True,
        outcome="ok",
        status=200,
        ttft_ms=120,
        duration_ms=900,
        output_tokens=40,
        output_tps=51.3,
    )
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_throttle():
    sig.reset_attempt_signal_prune_throttle()
    yield
    sig.reset_attempt_signal_prune_throttle()


async def test_record_and_list_roundtrip(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    rows = await sig.list_attempt_signals(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "m1"
    assert r["account_id"] == "acct-a"
    assert r["streamed"] == 1
    assert r["outcome"] == "ok"
    assert r["ttft_ms"] == 120
    assert r["output_tps"] == pytest.approx(51.3)
    assert r["timestamp"]


async def test_nullable_fields(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(
        db, **_row(status=None, ttft_ms=None, output_tps=None, streamed=False, outcome="error")
    )
    r = (await sig.list_attempt_signals(db))[0]
    assert r["status"] is None
    assert r["ttft_ms"] is None
    assert r["output_tps"] is None
    assert r["streamed"] == 0


async def test_record_swallows_errors(tmp_path):
    await sig.record_attempt_signal(tmp_path / "missing-dir" / "x" / "j.db", **_row())


async def test_prune_deletes_old_rows(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO attempt_signals (timestamp, model, account_id, outcome, duration_ms)"
            " VALUES (datetime('now', '-8 days'), 'old', 'acct-a', 'ok', 10)"
        )
        await conn.commit()
    deleted = await sig.prune_attempt_signals(db, 7)
    assert deleted == 1
    models = [r["model"] for r in await sig.list_attempt_signals(db)]
    assert models == ["m1"]


async def test_record_prunes_at_most_hourly(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    calls = []

    async def fake_prune(path, retention_days=7):
        calls.append(path)
        return 0

    monkeypatch.setattr(sig, "prune_attempt_signals", fake_prune)
    await sig.record_attempt_signal(db, **_row())
    await sig.record_attempt_signal(db, **_row())
    assert len(calls) == 1


async def test_init_db_is_idempotent_with_table(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    assert len(await sig.list_attempt_signals(db)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_attempt_signals.py -v`
Expected: FAIL (`ModuleNotFoundError: janus.storage.attempt_signals`).

- [ ] **Step 3: Add the table to the schema in `database.py`**

Directly after the `request_outcomes` `CREATE TABLE` block (before the `CREATE INDEX` lines) add:

```sql
CREATE TABLE IF NOT EXISTS attempt_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    model TEXT NOT NULL,
    provider_id TEXT,
    account_id TEXT NOT NULL,
    client_format TEXT,
    streamed INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL,
    status INTEGER,
    ttft_ms INTEGER,
    duration_ms INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    output_tps REAL
);
```

and alongside the other indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_attempt_signals_model_ts ON attempt_signals(model, timestamp);
CREATE INDEX IF NOT EXISTS idx_attempt_signals_account_ts ON attempt_signals(account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_attempt_signals_ts ON attempt_signals(timestamp);
```

Confirm by reading `init_db` that this schema string is executed for both fresh and existing
DBs (it is an `executescript` of `CREATE ... IF NOT EXISTS`). If indexes for legacy DBs are
created in a separate step, put the three index statements there instead.

- [ ] **Step 4: Create `src/janus/storage/attempt_signals.py`**

```python
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

ATTEMPT_SIGNAL_RETENTION_DAYS = 7
_PRUNE_MIN_INTERVAL_S = 3600.0
_last_prune: dict[str, float] = {}


def reset_attempt_signal_prune_throttle() -> None:
    _last_prune.clear()


async def record_attempt_signal(
    db_path: str | Path,
    *,
    model: str,
    account_id: str,
    provider_id: str | None,
    client_format: str | None,
    streamed: bool,
    outcome: str,
    status: int | None,
    ttft_ms: int | None,
    duration_ms: int,
    output_tokens: int,
    output_tps: float | None,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO attempt_signals
                   (model, provider_id, account_id, client_format, streamed, outcome,
                    status, ttft_ms, duration_ms, output_tokens, output_tps)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model,
                    provider_id,
                    account_id,
                    client_format,
                    1 if streamed else 0,
                    outcome,
                    status,
                    ttft_ms,
                    max(duration_ms, 0),
                    max(output_tokens, 0),
                    output_tps,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record attempt signal: %s", e)
        return
    await _maybe_prune(db_path)


async def _maybe_prune(db_path: str | Path) -> None:
    key = str(db_path)
    now = time.monotonic()
    last = _last_prune.get(key)
    if last is not None and now - last < _PRUNE_MIN_INTERVAL_S:
        return
    _last_prune[key] = now
    try:
        await prune_attempt_signals(db_path, ATTEMPT_SIGNAL_RETENTION_DAYS)
    except Exception as e:
        logger.warning("Attempt signal prune failed: %s", e)


async def prune_attempt_signals(
    db_path: str | Path, retention_days: int = ATTEMPT_SIGNAL_RETENTION_DAYS
) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "DELETE FROM attempt_signals WHERE timestamp < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def list_attempt_signals(db_path: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, timestamp, model, provider_id, account_id, client_format, streamed,
                      outcome, status, ttft_ms, duration_ms, output_tokens, output_tps
               FROM attempt_signals ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]
```

Note: `_maybe_prune` must call the module attribute `prune_attempt_signals` by name (as written)
so the monkeypatch test works. `test_record_swallows_errors` passes a path whose parent does not
exist; if `get_connection` creates directories instead of raising, change that test to
monkeypatch `sig.get_connection` with a context manager that raises `RuntimeError`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_attempt_signals.py -v`
Expected: all PASS. Also run `.venv/bin/python scripts/migration_smoke.py` — expected PASS.

- [ ] **Step 6: Commit**

```bash
git add src/janus/storage/database.py src/janus/storage/attempt_signals.py tests/unit/storage/test_attempt_signals.py
git commit -m "feat(storage): attempt_signals table with throttled retention prune (#187)"
```

---

### Task 2: First-content timestamp on `StreamUsageTracker`

**Files:**
- Modify: `src/janus/streaming/usage.py`
- Test: `tests/unit/streaming/test_usage.py` (append)

**Interfaces:**
- Produces: `StreamUsageTracker.first_content_at: float | None` — `time.monotonic()` of the
  first content event, set once.

Content events: `TextDelta` with non-empty `text`, `ReasoningDelta` with non-empty `text`,
`ToolUseBlockStart`, `InputJsonDelta`. All from `janus.canonical.events`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/streaming/test_usage.py`)

```python
import types

from janus.canonical.events import (
    InputJsonDelta,
    MessageStart,
    ReasoningDelta,
    TextDelta,
    ToolUseBlockStart,
)
from janus.streaming.usage import StreamUsageTracker


class _ListParser:
    def __init__(self, batches):
        self._batches = list(batches)

    def feed(self, line):
        return self._batches.pop(0) if self._batches else []

    def finish(self):
        return []


def test_first_content_at_none_without_content():
    t = StreamUsageTracker(_ListParser([[MessageStart(model="m")], [TextDelta(index=0, text="")]]))
    t.feed("a")
    t.feed("b")
    assert t.first_content_at is None


def test_first_content_at_set_on_first_text(monkeypatch):
    clock = iter([10.0, 20.0])
    monkeypatch.setattr(
        "janus.streaming.usage.time", types.SimpleNamespace(monotonic=lambda: next(clock))
    )
    t = StreamUsageTracker(
        _ListParser([[TextDelta(index=0, text="hi")], [TextDelta(index=0, text="there")]])
    )
    t.feed("a")
    t.feed("b")
    assert t.first_content_at == 10.0


def test_reasoning_and_tool_events_count_as_content():
    for event in (
        ReasoningDelta(index=0, text="thinking"),
        ToolUseBlockStart(index=0, id="t1", name="f"),
        InputJsonDelta(index=0, partial_json="{"),
    ):
        t = StreamUsageTracker(_ListParser([[event]]))
        t.feed("a")
        assert t.first_content_at is not None


def test_empty_reasoning_signature_only_is_not_content():
    t = StreamUsageTracker(_ListParser([[ReasoningDelta(index=0, text="", signature="s")]]))
    t.feed("a")
    assert t.first_content_at is None
```

If `_ListParser` does not satisfy the `StreamParser` type at runtime this is fine (tests are not
type-checked); if `StreamUsageTracker.__init__` does more than store the parser, adapt the stub.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/streaming/test_usage.py -v`
Expected: new tests FAIL (`AttributeError: first_content_at`).

- [ ] **Step 3: Implement**

In `src/janus/streaming/usage.py`: add `import time`; extend the events import to
`CanonicalEvent, InputJsonDelta, MessageDelta, ReasoningDelta, TextDelta, ToolUseBlockStart`;
in `__init__` add `self.first_content_at: float | None = None`; in `_collect`, at the top of the
loop body:

```python
            if self.first_content_at is None and _is_content(event):
                self.first_content_at = time.monotonic()
```

and add a module-level function:

```python
def _is_content(event: CanonicalEvent) -> bool:
    if isinstance(event, TextDelta | ReasoningDelta):
        return bool(event.text)
    return isinstance(event, ToolUseBlockStart | InputJsonDelta)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/streaming/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/streaming/usage.py tests/unit/streaming/test_usage.py
git commit -m "feat(streaming): track first content event time in StreamUsageTracker (#187)"
```

---

### Task 3: `AttemptSignal` glue, TPS math, background task set

**Files:**
- Create: `src/janus/api/signals.py`
- Test: `tests/unit/api/test_signals.py`

**Interfaces:**
- Consumes: `record_attempt_signal` (Task 1).
- Produces:
  - `MIN_TPS_WINDOW_S: float = 0.05`
  - `def compute_output_tps(output_tokens: int, seconds: float) -> float | None`
  - `class AttemptSignal` with `__init__(self, db_path: str | Path, *, model: str, account_id: str, provider_id: str | None, client_format: str | None, streamed: bool)`,
    attributes `started_at: float`, `finished: bool`, and
    `def finish(self, outcome: str, *, status: int | None, output_tokens: int = 0, first_content_at: float | None = None) -> None`
  - `async def drain_attempt_signal_tasks() -> None`

Semantics of `finish` (idempotent — second call is a no-op):
- `ended = time.monotonic()`; `duration_ms = int((ended - started_at) * 1000)`.
- `ttft_ms = int((first_content_at - started_at) * 1000)` when `first_content_at` is not None,
  else None.
- If `streamed` and `outcome == "ok"` and `first_content_at` is not None:
  `tps = compute_output_tps(output_tokens, ended - first_content_at)`.
  If not `streamed` and `outcome == "ok"`: `tps = compute_output_tps(output_tokens, ended - started_at)`.
  Otherwise `tps = None`.
- Schedule `record_attempt_signal(...)` via `loop.create_task`, tracked in a module set with a
  done-callback that discards and logs exceptions. With no running loop, close the coroutine and
  return.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio
import types

import pytest

from janus.api import signals
from janus.storage.attempt_signals import list_attempt_signals, reset_attempt_signal_prune_throttle
from janus.storage.database import init_db


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()


def test_compute_output_tps():
    assert signals.compute_output_tps(0, 1.0) is None
    assert signals.compute_output_tps(10, 0.01) is None
    assert signals.compute_output_tps(100, 2.0) == pytest.approx(50.0)


def _sig(db, streamed=True):
    return signals.AttemptSignal(
        db,
        model="m1",
        account_id="acct-a",
        provider_id="acct-a",
        client_format="openai",
        streamed=streamed,
    )


async def test_stream_ok_records_ttft_and_tps(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([100.0, 103.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    s = _sig(db)
    s.finish("ok", status=200, output_tokens=100, first_content_at=101.0)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["outcome"] == "ok"
    assert r["ttft_ms"] == 1000
    assert r["duration_ms"] == 3000
    assert r["output_tps"] == pytest.approx(50.0)
    assert r["streamed"] == 1


async def test_nonstream_ok_tps_uses_full_duration(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([0.0, 2.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    s = _sig(db, streamed=False)
    s.finish("ok", status=200, output_tokens=40)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["ttft_ms"] is None
    assert r["output_tps"] == pytest.approx(20.0)
    assert r["streamed"] == 0


async def test_error_has_no_tps(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    _sig(db).finish("error", status=500, output_tokens=5, first_content_at=None)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["outcome"] == "error"
    assert r["status"] == 500
    assert r["output_tps"] is None


async def test_aborted_keeps_ttft_drops_tps(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([0.0, 5.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    _sig(db).finish("aborted", status=499, output_tokens=50, first_content_at=0.5)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["ttft_ms"] == 500
    assert r["output_tps"] is None


async def test_finish_is_idempotent(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    s = _sig(db)
    s.finish("error", status=500)
    s.finish("ok", status=200)
    await signals.drain_attempt_signal_tasks()
    rows = await list_attempt_signals(db)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert s.finished is True


async def test_unfinished_signal_writes_nothing(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    _sig(db)
    await signals.drain_attempt_signal_tasks()
    assert await list_attempt_signals(db) == []


def test_finish_without_loop_does_not_raise(tmp_path):
    s = _sig(tmp_path / "j.db")
    s.finish("ok", status=200)
    assert s.finished is True


async def test_drain_survives_failing_task(monkeypatch, tmp_path):
    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(signals, "record_attempt_signal", boom)
    _sig(tmp_path / "j.db").finish("ok", status=200)
    await signals.drain_attempt_signal_tasks()
    await asyncio.sleep(0)
```

Create `tests/unit/api/__init__.py` only if `tests/unit/api/` lacks one and sibling test dirs
have one.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/api/test_signals.py -v`
Expected: FAIL (`ModuleNotFoundError: janus.api.signals`).

- [ ] **Step 3: Implement `src/janus/api/signals.py`**

```python
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from janus.storage.attempt_signals import record_attempt_signal

logger = logging.getLogger(__name__)

MIN_TPS_WINDOW_S = 0.05

_signal_tasks: set[asyncio.Task[None]] = set()


def compute_output_tps(output_tokens: int, seconds: float) -> float | None:
    if output_tokens <= 0 or seconds < MIN_TPS_WINDOW_S:
        return None
    return output_tokens / seconds


def _on_done(task: asyncio.Task[None]) -> None:
    _signal_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("Attempt signal write failed: %s", error)


async def drain_attempt_signal_tasks() -> None:
    while _signal_tasks:
        pending = list(_signal_tasks)
        _signal_tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)


class AttemptSignal:
    def __init__(
        self,
        db_path: str | Path,
        *,
        model: str,
        account_id: str,
        provider_id: str | None,
        client_format: str | None,
        streamed: bool,
    ) -> None:
        self._db_path = db_path
        self._model = model
        self._account_id = account_id
        self._provider_id = provider_id
        self._client_format = client_format
        self._streamed = streamed
        self.started_at = time.monotonic()
        self.finished = False

    def finish(
        self,
        outcome: str,
        *,
        status: int | None,
        output_tokens: int = 0,
        first_content_at: float | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        ended = time.monotonic()
        ttft_ms = (
            int((first_content_at - self.started_at) * 1000)
            if first_content_at is not None
            else None
        )
        tps: float | None = None
        if outcome == "ok":
            if self._streamed:
                if first_content_at is not None:
                    tps = compute_output_tps(output_tokens, ended - first_content_at)
            else:
                tps = compute_output_tps(output_tokens, ended - self.started_at)
        coro = record_attempt_signal(
            self._db_path,
            model=self._model,
            account_id=self._account_id,
            provider_id=self._provider_id,
            client_format=self._client_format,
            streamed=self._streamed,
            outcome=outcome,
            status=status,
            ttft_ms=ttft_ms,
            duration_ms=int((ended - self.started_at) * 1000),
            output_tokens=output_tokens,
            output_tps=tps,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        _signal_tasks.add(task)
        task.add_done_callback(_on_done)
```

`finish` must look up `record_attempt_signal` as a module global at call time (as written) so
`monkeypatch.setattr(signals, "record_attempt_signal", ...)` works.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/api/test_signals.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/signals.py tests/unit/api/
git commit -m "feat(api): AttemptSignal per-attempt timing recorder (#187)"
```

---

### Task 4: Wire signals into `_handle()` on every attempt path

**Files:**
- Modify: `src/janus/api/routes.py`
- Test: `tests/integration/test_attempt_signals.py` (new)

**Interfaces:**
- Consumes: `AttemptSignal`, `drain_attempt_signal_tasks` (Task 3); `tracker.first_content_at`
  (Task 2); `list_attempt_signals` (Task 1).
- Produces: `_OutcomeRecorder.attempt_signal: AttemptSignal | None` attribute (default None);
  `_note_attempt_failure(failed, detail, *, status: int | None = None)`.

Changes, in order (line numbers are approximate; locate by the quoted code):

1. Import: `from janus.api.signals import AttemptSignal, drain_attempt_signal_tasks`.
2. `_OutcomeRecorder.__init__`: add `self.attempt_signal: AttemptSignal | None = None`.
3. `_drain_stream_persist_tasks()`: after the existing loop, add
   `await drain_attempt_signal_tasks()`.
4. `_log_error_and_raise`: as the first statement:

   ```python
   signal = outcome.attempt_signal
   if signal is not None and not signal.finished:
       signal.finish("client_error" if 400 <= status < 500 else "error", status=status)
   ```

5. `_handle` catch-all `except Exception:` (the branch recording `status=500`, ~line 757): before
   `outcome.record`, add the same pattern with `signal.finish("error", status=500)`. Leave the
   `HTTPException` branch alone (`_log_error_and_raise` already handled those).
6. Inside `_handle_with_snapshot`, add a local helper next to `_elapsed_ms`:

   ```python
   def _start_signal(target: ResolvedTarget, streamed: bool) -> AttemptSignal:
       signal = AttemptSignal(
           db_path,
           model=target.model,
           account_id=target.account_id,
           provider_id=target.provider_config.id,
           client_format=client_format,
           streamed=streamed,
       )
       outcome.attempt_signal = signal
       return signal
   ```

7. `_note_attempt_failure(failed, detail, *, status: int | None = None)`: first statement:

   ```python
   signal = outcome.attempt_signal
   if signal is not None and not signal.finished:
       signal.finish("error", status=status)
   ```

   Update every caller to pass `status=`: `str(result.status_code)` / `str(native_result.status_code)`
   callers pass that int; `"200-wrapped quota error"` callers pass `status=200`;
   `"Empty completion ..."` passes `status=result.status_code`; `type(e).__name__` network
   callers pass nothing (None).
8. At the top of the `for target in attempts:` loop body (before `is_available`), add
   `outcome.attempt_signal = None`.
9. Start a signal immediately before each upstream call:
   - Transport passthrough: before `result = await _passthrough_call(`, add
     `pt_signal = _start_signal(target, attempt_req.stream)`.
   - Native passthrough: before `native_result = await provider_p.call(`, add
     `native_signal = _start_signal(target, attempt_req.stream)`.
   - Canonical: right after `handler.record_attempt(target)` (the one followed by
     `provider_kwargs = _claude_provider_kwargs(`), add
     `canon_signal = _start_signal(target, attempt_req.stream)`.
10. Non-stream success: immediately after each success-path `handler.mark_success(target.account_id, target.model)`
    that is followed by `await outcome.record(status=200 ...)` / `status=result.status_code`:
    - transport: `pt_signal.finish("ok", status=200, output_tokens=pt_usage.output_tokens)`
    - native: `native_signal.finish("ok", status=200, output_tokens=passthrough_usage.output_tokens)`
    - canonical: `canon_signal.finish("ok", status=result.status_code, output_tokens=canonical_resp.usage.output_tokens)`
11. Streaming `finally` blocks (three: `_pt_stream`, `_native_stream`, `_streaming_generator`):
    immediately after `usage = tracker.get_usage()` and once `final_status` is computed, add
    (using the matching signal name):

    ```python
    pt_signal.finish(
        "aborted" if client_aborted else ("ok" if final_status < 400 else "error"),
        status=final_status,
        output_tokens=usage.output_tokens,
        first_content_at=tracker.first_content_at,
    )
    ```

    If the `final_status` assignment comes after `usage = ...` in a block, place the call after
    both. The call is synchronous (it schedules a task), so it is safe in the cancelled scope.

- [ ] **Step 1: Write the failing integration tests** (`tests/integration/test_attempt_signals.py`)

Reuse the `GOOD_BODY`, `GOOD_SSE`, `_seed_and_reload`, `one_account_app`, `two_account_app`
patterns from `tests/integration/test_request_outcomes.py` (copy them into this file). Then:

```python
import asyncio
from contextlib import suppress

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.storage.attempt_signals import list_attempt_signals, reset_attempt_signal_prune_throttle


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()


async def _signals(app):
    await _drain_stream_persist_tasks()
    return sorted(await list_attempt_signals(app.state.db_path), key=lambda r: r["id"])


def _body(**kw):
    b = {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}
    b.update(kw)
    return b


@respx.mock
async def test_fallback_records_error_then_ok(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(500))
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 200
    rows = await _signals(two_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("error", 500), ("ok", 200)]
    assert rows[0]["account_id"] != rows[1]["account_id"]
    assert rows[1]["output_tokens"] == 4
    assert rows[1]["streamed"] == 0


@respx.mock
async def test_network_error_records_null_status(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        await c.post("http://test/v1/chat/completions", json=_body())
    rows = await _signals(two_account_app)
    assert rows[0]["outcome"] == "error"
    assert rows[0]["status"] is None


@respx.mock
async def test_stream_success_records_ttft(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=GOOD_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body(stream=True))
        assert r.status_code == 200
    rows = await _signals(one_account_app)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["streamed"] == 1
    assert rows[0]["ttft_ms"] is not None and rows[0]["ttft_ms"] >= 0


@respx.mock
async def test_non_eligible_400_is_client_error(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad"}})
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 400
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("client_error", 400)]


@respx.mock
async def test_exhausted_records_one_row_per_attempt(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(503))
    respx.post("https://b.local/v1/chat/completions").mock(return_value=httpx.Response(503))
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 503
    rows = await _signals(two_account_app)
    assert [x["outcome"] for x in rows] == ["error", "error"]


@respx.mock
async def test_unexpected_exception_records_error_signal(one_account_app, monkeypatch):
    from janus.formats import openai as openai_fmt

    def explode(self, data):
        raise RuntimeError("parse bug")

    monkeypatch.setattr(openai_fmt.OpenAIAdapter, "parse_upstream_response", explode)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        with suppress(Exception):
            await c.post("http://test/v1/chat/completions", json=_body())
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("error", 500)]
```

For `test_unexpected_exception_records_error_signal`: check the real adapter class name in
`src/janus/formats/openai.py` and whether this provider path is native passthrough (openai →
openai_compat is the native path, which wraps `parse_upstream_response` in `try/except` and
would swallow the error). If the native path swallows it, instead monkeypatch
`janus.api.routes.record_usage` to raise `RuntimeError`, which escapes on every success path.

Add the prompt-cache and abort tests:

```python
@respx.mock
async def test_prompt_cache_hit_records_no_signal(one_account_app):
    from janus.storage.settings import set_setting

    await set_setting(one_account_app.state.db_path, "server_prompt_cache", "1")
    route = respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        await c.post("http://test/v1/chat/completions", json=_body(temperature=0))
        await c.post("http://test/v1/chat/completions", json=_body(temperature=0))
    rows = await _signals(one_account_app)
    assert len(rows) == route.call_count
```

Look up the real prompt-cache setting key and the conditions that make a request cacheable in
`tests/integration/test_prompt_cache.py` and use those; the assertion (one signal row per real
upstream call) stays.

For the abort case, copy `test_cancelled_stream_persists_usage_outcome_and_log` from
`tests/integration/test_stream_abort_telemetry.py` (its `ROLE_LINE`, `CONTENT_LINE`,
`REQUEST_BODY`, fixture) into a test named `test_client_abort_records_aborted_signal` and replace
the final assertions with:

```python
    rows = await _signals(app)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "aborted"
    assert rows[0]["ttft_ms"] is not None
    assert rows[0]["output_tps"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/integration/test_attempt_signals.py -v`
Expected: FAIL (no rows recorded).

- [ ] **Step 3: Implement changes 1–11 above.**

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/integration/test_attempt_signals.py tests/integration/test_request_outcomes.py tests/integration/test_stream_abort_telemetry.py tests/integration/test_stream_fallback.py tests/integration/test_transport_fallback.py tests/integration/test_fallback_attempt_trail.py -v`
Expected: PASS. Then the full suite: `.venv/bin/python -m pytest -q` — expected PASS.

- [ ] **Step 5: Lint and type-check**

Run: `.venv/bin/python -m ruff check src/ tests/ scripts/ && .venv/bin/python -m ruff format --check src/ tests/ scripts/ && .venv/bin/python -m mypy src/janus/ scripts/`
Expected: clean (run `ruff format` on touched files if needed).

- [ ] **Step 6: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_attempt_signals.py
git commit -m "feat(routing): record per-attempt signals on every request path (#187)"
```

---

### Task 5: Cached aggregate `get_model_signals()`

**Files:**
- Create: `src/janus/storage/model_signals.py`
- Modify: `src/janus/dashboard/reload.py` (`reload_providers`)
- Test: `tests/unit/storage/test_model_signals.py`

**Interfaces:**
- Consumes: `attempt_signals` table (Task 1).
- Produces:
  - `SUFFICIENT_SAMPLES = 5`, `CACHE_TTL_S = 60.0`
  - `@dataclass(frozen=True) class SignalStats: samples: int; errors: int; error_rate: float | None; ttft_p50_ms: int | None; ttft_p90_ms: int | None; tps_p50: float | None; tps_p90: float | None; nonstream_tps_p50: float | None; last_seen: str | None; sufficient: bool`
  - `@dataclass(frozen=True) class ModelSignals: by_model: dict[str, SignalStats]; by_account: dict[tuple[str, str], SignalStats]`
  - `def percentile(values: list[float], pct: float) -> float | None` (nearest-rank: sorted,
    index `ceil(pct/100 * n) - 1`, clamped to `[0, n-1]`; None for empty)
  - `async def get_model_signals(db_path: str | Path, *, window_days: int = 7, max_rows: int = 50_000) -> ModelSignals`
  - `def invalidate_model_signals_cache(db_path: str | Path | None = None) -> None`

Aggregation rules per group:
- `samples` = rows with outcome in (`ok`, `error`); `errors` = `error` rows;
  `error_rate = errors / samples` or None when samples == 0.
- TTFT percentiles over non-null `ttft_ms` of `ok` rows with `streamed = 1`.
- `tps_p50`/`tps_p90` over non-null `output_tps` of `ok`, `streamed = 1` rows;
  `nonstream_tps_p50` over `ok`, `streamed = 0` rows.
- `last_seen` = max `timestamp` among all rows of the group (any outcome).
- `sufficient = samples >= SUFFICIENT_SAMPLES`.
- TTFT percentiles returned as `int` (rounded).

Cache: dict keyed by `(str(db_path), window_days, max_rows)` → `(expires_monotonic, ModelSignals)`;
in-flight dict of `asyncio.Task[ModelSignals]` so concurrent misses share one query. On any DB
error log a warning and return (and cache) `ModelSignals({}, {})`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio

import pytest

from janus.storage import model_signals as ms
from janus.storage.attempt_signals import record_attempt_signal, reset_attempt_signal_prune_throttle
from janus.storage.database import get_connection, init_db


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()
    ms.invalidate_model_signals_cache()
    yield
    ms.invalidate_model_signals_cache()


async def _add(db, *, model="m1", account="a", outcome="ok", streamed=True, ttft=100, tps=50.0):
    await record_attempt_signal(
        db,
        model=model,
        account_id=account,
        provider_id=account,
        client_format="openai",
        streamed=streamed,
        outcome=outcome,
        status=200 if outcome == "ok" else 500,
        ttft_ms=ttft,
        duration_ms=1000,
        output_tokens=10,
        output_tps=tps,
    )


def test_percentile_nearest_rank():
    assert ms.percentile([], 50) is None
    assert ms.percentile([5.0], 90) == 5.0
    vals = [float(v) for v in range(1, 11)]
    assert ms.percentile(vals, 50) == 5.0
    assert ms.percentile(vals, 90) == 9.0


async def test_aggregates_by_model_and_account(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    for ttft in (100, 200, 300, 400):
        await _add(db, account="a", ttft=ttft, tps=float(ttft))
    await _add(db, account="b", outcome="error", ttft=None, tps=None)
    await _add(db, account="b", outcome="client_error", ttft=None, tps=None)
    await _add(db, account="b", outcome="aborted", ttft=50, tps=None)
    out = await ms.get_model_signals(db)
    m = out.by_model["m1"]
    assert m.samples == 5
    assert m.errors == 1
    assert m.error_rate == pytest.approx(0.2)
    assert m.ttft_p50_ms == 200
    assert m.ttft_p90_ms == 400
    assert m.sufficient is True
    b = out.by_account[("m1", "b")]
    assert b.samples == 1 and b.errors == 1 and b.error_rate == 1.0
    assert b.ttft_p50_ms is None
    assert b.sufficient is False
    assert b.last_seen is not None


async def test_stream_and_nonstream_tps_split(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db, streamed=True, tps=80.0)
    await _add(db, streamed=False, ttft=None, tps=20.0)
    m = (await ms.get_model_signals(db)).by_model["m1"]
    assert m.tps_p50 == 80.0
    assert m.nonstream_tps_p50 == 20.0


async def test_window_excludes_old_rows(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO attempt_signals (timestamp, model, account_id, outcome, duration_ms)"
            " VALUES (datetime('now', '-3 days'), 'old', 'a', 'ok', 10)"
        )
        await conn.commit()
    out = await ms.get_model_signals(db, window_days=1)
    assert "old" not in out.by_model
    assert "m1" in out.by_model


async def test_max_rows_bounds_scan(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    for _ in range(5):
        await _add(db)
    out = await ms.get_model_signals(db, max_rows=3)
    assert out.by_model["m1"].samples == 3


async def test_cache_and_invalidation(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    first = await ms.get_model_signals(db)
    await _add(db)
    assert (await ms.get_model_signals(db)).by_model["m1"].samples == first.by_model["m1"].samples
    ms.invalidate_model_signals_cache(db)
    assert (await ms.get_model_signals(db)).by_model["m1"].samples == 2


async def test_concurrent_misses_share_one_query(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    calls = 0
    real = ms._query

    async def counting(*a, **k):
        nonlocal calls
        calls += 1
        return await real(*a, **k)

    monkeypatch.setattr(ms, "_query", counting)
    await asyncio.gather(*(ms.get_model_signals(db) for _ in range(5)))
    assert calls == 1


async def test_db_error_returns_empty(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ms, "_query", boom)
    out = await ms.get_model_signals(tmp_path / "j.db")
    assert out.by_model == {} and out.by_account == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_model_signals.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/janus/storage/model_signals.py`**

```python
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

SUFFICIENT_SAMPLES = 5
CACHE_TTL_S = 60.0

_CacheKey = tuple[str, int, int]


@dataclass(frozen=True)
class SignalStats:
    samples: int
    errors: int
    error_rate: float | None
    ttft_p50_ms: int | None
    ttft_p90_ms: int | None
    tps_p50: float | None
    tps_p90: float | None
    nonstream_tps_p50: float | None
    last_seen: str | None
    sufficient: bool


@dataclass(frozen=True)
class ModelSignals:
    by_model: dict[str, SignalStats]
    by_account: dict[tuple[str, str], SignalStats]


_cache: dict[_CacheKey, tuple[float, ModelSignals]] = {}
_inflight: dict[_CacheKey, asyncio.Task[ModelSignals]] = {}


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[min(max(idx, 0), len(ordered) - 1)]


def invalidate_model_signals_cache(db_path: str | Path | None = None) -> None:
    if db_path is None:
        _cache.clear()
        return
    prefix = str(db_path)
    for key in [k for k in _cache if k[0] == prefix]:
        _cache.pop(key, None)


async def _query(db_path: str | Path, window_days: int, max_rows: int) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT timestamp, model, account_id, streamed, outcome, ttft_ms, output_tps
               FROM attempt_signals
               WHERE timestamp >= datetime('now', ?)
               ORDER BY id DESC LIMIT ?""",
            (f"-{int(window_days)} days", int(max_rows)),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


class _Acc:
    def __init__(self) -> None:
        self.samples = 0
        self.errors = 0
        self.ttft: list[float] = []
        self.tps: list[float] = []
        self.nonstream_tps: list[float] = []
        self.last_seen: str | None = None

    def add(self, row: dict[str, Any]) -> None:
        ts = row["timestamp"]
        if self.last_seen is None or ts > self.last_seen:
            self.last_seen = ts
        outcome = row["outcome"]
        if outcome not in ("ok", "error"):
            return
        self.samples += 1
        if outcome == "error":
            self.errors += 1
            return
        if row["streamed"]:
            if row["ttft_ms"] is not None:
                self.ttft.append(float(row["ttft_ms"]))
            if row["output_tps"] is not None:
                self.tps.append(float(row["output_tps"]))
        elif row["output_tps"] is not None:
            self.nonstream_tps.append(float(row["output_tps"]))

    def stats(self) -> SignalStats:
        p50 = percentile(self.ttft, 50)
        p90 = percentile(self.ttft, 90)
        return SignalStats(
            samples=self.samples,
            errors=self.errors,
            error_rate=self.errors / self.samples if self.samples else None,
            ttft_p50_ms=round(p50) if p50 is not None else None,
            ttft_p90_ms=round(p90) if p90 is not None else None,
            tps_p50=percentile(self.tps, 50),
            tps_p90=percentile(self.tps, 90),
            nonstream_tps_p50=percentile(self.nonstream_tps, 50),
            last_seen=self.last_seen,
            sufficient=self.samples >= SUFFICIENT_SAMPLES,
        )


def _aggregate(rows: list[dict[str, Any]]) -> ModelSignals:
    by_model: dict[str, _Acc] = {}
    by_account: dict[tuple[str, str], _Acc] = {}
    for row in rows:
        by_model.setdefault(row["model"], _Acc()).add(row)
        by_account.setdefault((row["model"], row["account_id"]), _Acc()).add(row)
    return ModelSignals(
        by_model={k: v.stats() for k, v in by_model.items()},
        by_account={k: v.stats() for k, v in by_account.items()},
    )


async def _load(db_path: str | Path, window_days: int, max_rows: int) -> ModelSignals:
    try:
        rows = await _query(db_path, window_days, max_rows)
    except Exception as e:
        logger.warning("Failed to load model signals: %s", e)
        return ModelSignals({}, {})
    return _aggregate(rows)


async def get_model_signals(
    db_path: str | Path, *, window_days: int = 7, max_rows: int = 50_000
) -> ModelSignals:
    key: _CacheKey = (str(db_path), window_days, max_rows)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(_load(db_path, window_days, max_rows))
        _inflight[key] = task
        try:
            result = await task
        finally:
            _inflight.pop(key, None)
        _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
        return result
    return await task
```

`_load` must call `_query` as a module global (as written) for the monkeypatch tests.

- [ ] **Step 4: Invalidate on provider reload**

In `src/janus/dashboard/reload.py` add
`from janus.storage.model_signals import invalidate_model_signals_cache` and call
`invalidate_model_signals_cache(app.state.db_path)` inside `reload_providers` after the new
providers/registry are installed (inside the lock, at the end of the successful path). Add a test
to `tests/unit/storage/test_model_signals.py`:

```python
async def test_reload_providers_invalidates_cache(tmp_path, monkeypatch):
    from janus.dashboard import reload as reload_mod

    seen = []
    monkeypatch.setattr(reload_mod, "invalidate_model_signals_cache", lambda p=None: seen.append(p))
    from janus.app import create_app
    from janus.config.schema import JanusConfig, ServerSettings
    from janus.storage.database import init_db as _init

    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    await _init(app.state.db_path)
    await reload_mod.reload_providers(app)
    assert seen == [app.state.db_path]
```

- [ ] **Step 5: Run tests + lint + mypy**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_model_signals.py -v && .venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m mypy src/janus/`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add src/janus/storage/model_signals.py src/janus/dashboard/reload.py tests/unit/storage/test_model_signals.py
git commit -m "feat(storage): cached per-model/per-account signal aggregate (#187)"
```

---

### Task 6: Docs, changelog, full gates

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Added`)
- Modify: `AGENTS.md` (Routing & fallback layer section: one bullet)

- [ ] **Step 1: CHANGELOG entry** under `## [Unreleased]` / `### Added`:

```markdown
- **Per-attempt latency and reliability signals** — every upstream attempt now
  records time-to-first-token, output tokens/sec, and its outcome
  (`ok` / `error` / `client_error` / `aborted`) per model and account in a new
  `attempt_signals` table (7-day retention). Fallback-masked failures now count
  against the failing account. Data collection only; ranking arrives with
  `model="auto"`. (#187)
```

- [ ] **Step 2: AGENTS.md bullet** in "Routing & fallback layer":

```markdown
- Per-attempt signals: `api/signals.py::AttemptSignal` is started before each upstream call
  (hung on `_OutcomeRecorder.attempt_signal`) and finished exactly once from
  `_note_attempt_failure` (`error`), `_log_error_and_raise` (`client_error` for 4xx), success
  paths, and stream `finally` blocks (`ok`/`aborted`/`error`). Writes go to `attempt_signals`
  in tracked background tasks drained on shutdown. `storage/model_signals.py::get_model_signals`
  aggregates p50/p90 TTFT and tokens/sec plus error rate (60 s cache, invalidated in
  `reload_providers`). Signals are data only until #183 consumes them; they must never block.
```

- [ ] **Step 3: Full gates**

Run each, all must pass:

```bash
.venv/bin/python -m ruff check src/ tests/ scripts/
.venv/bin/python -m ruff format --check src/ tests/ scripts/
.venv/bin/python -m mypy src/janus/ scripts/
.venv/bin/python -m pytest --cov=janus --cov-fail-under=80 -q
.venv/bin/python scripts/migration_smoke.py
.venv/bin/mkdocs build --strict
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md AGENTS.md
git commit -m "docs: document per-attempt signals (#187)"
```
