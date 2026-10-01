# Per-attempt latency and reliability signals (#187 part 1)

Date: 2026-10-01. Status: draft for review. Issue: #187 (tracked in #239).

## Goal

Start capturing per-attempt time-to-first-token (TTFT), output tokens/sec, and error outcomes for
every upstream attempt, per model and per account, and expose a cached aggregate. Data must
accumulate before #183 (`model="auto"` strategies) and #187 part 2 (preview table, overrides) rank
on it.

Out of scope for this part: routing changes, dashboard UI, state sections, operator overrides,
the auto-preview, and `janus bench` (deferred to a follow-up issue by user decision).

Success: after a fallback A→B, the DB holds one `error` row for account A and one `ok` row for
account B; a streamed success records `ttft_ms` and `output_tps`; `get_model_signals()` returns
p50/p90 TTFT and tokens/sec plus an error rate per model and per (model, account).

## Current behavior (verified in code)

- `request_outcomes` records exactly one row per client request (`_OutcomeRecorder`); fallback
  attempts are folded into `attempts`. Its `duration_ms` is measured from request start, so it
  includes time spent on failed attempts.
- `usage` rows carry tokens and cost, no timing. No TTFT is captured anywhere.
- `_handle()` has three streaming paths (transport passthrough, native passthrough, canonical
  cross-format), each wrapping its parser in `StreamUsageTracker`, plus non-stream paths. Every
  fallback-eligible failure goes through the local `_note_attempt_failure(target, detail)` hook.
  Non-eligible upstream errors go through `_log_error_and_raise`.

## Design

### 1. Storage: `attempt_signals`

New table, created by `init_db` (`CREATE TABLE IF NOT EXISTS`, idempotent):

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `timestamp` | TEXT NOT NULL DEFAULT `datetime('now')` | UTC |
| `model` | TEXT NOT NULL | physical model (`target.model`) |
| `provider_id` | TEXT | `target.provider_config.id` |
| `account_id` | TEXT NOT NULL | `target.account_id` |
| `client_format` | TEXT | |
| `streamed` | INTEGER NOT NULL DEFAULT 0 | `0` marks lower-fidelity TPS |
| `outcome` | TEXT NOT NULL | `ok` / `error` / `client_error` / `aborted` |
| `status` | INTEGER | HTTP status when known |
| `ttft_ms` | INTEGER NULL | streams only |
| `duration_ms` | INTEGER NOT NULL | this attempt only |
| `output_tokens` | INTEGER NOT NULL DEFAULT 0 | |
| `output_tps` | REAL NULL | |

Indexes: `(model, timestamp)`, `(account_id, timestamp)`, `(timestamp)` for pruning.

Outcome classification:

- `ok`: the attempt produced the response returned to the client (stream completed or JSON
  returned).
- `error`: every `_note_attempt_failure` call (429, 5xx, auth, network exceptions, 200-wrapped
  quota errors, empty completions) and a stream that failed upstream mid-flight (`final_status`
  502).
- `client_error`: a non-fallback-eligible upstream 4xx raised via `_log_error_and_raise`. Not the
  account's fault; excluded from the error-rate denominator.
- `aborted`: the client disconnected mid-stream. Excluded from error rate; TTFT still recorded
  if seen, TPS null.

Retention: rows older than 7 days (`ATTEMPT_SIGNAL_RETENTION_DAYS = 7`) are deleted, throttled to
once per hour per DB path, using the same pattern as `storage/usage.py::_maybe_prune_retention`.

### 2. Measurement

All timing lives in `api/routes.py` and `streaming/usage.py`. `formats/` and `providers/` are
unchanged.

- An `_AttemptClock` (monotonic `started_at`) is created for each attempt immediately before the
  upstream call (`_passthrough_call`, `provider_p.call`, `provider.call`).
- `StreamUsageTracker` gains `first_content_at: float | None`, set (via `time.monotonic()`) on the
  first `TextDelta` with non-empty text, `ReasoningDelta` with non-empty text, `ToolUseBlockStart`,
  or `InputJsonDelta`. Bookkeeping events (`MessageStart`, block starts without content,
  `MessageDelta`) do not count.
- TTFT = `first_content_at − started_at` (ms). Null when no content event arrived.
- Stream TPS = `output_tokens / (ended_at − first_content_at)` in seconds. Null when
  `output_tokens == 0`, no content arrived, or the generation phase is under 50 ms (too short to
  measure meaningfully).
- Non-stream TPS = `output_tokens / duration`, null when `output_tokens == 0` or duration is
  under 50 ms. `streamed = 0` distinguishes these rows.
- A pure function `compute_output_tps(output_tokens, seconds) -> float | None` holds the math and
  the 50 ms floor so both paths share it.

### 3. Recording

- `storage/attempt_signals.py::record_attempt_signal(db_path, **fields)` inserts one row,
  swallows and logs exceptions, then calls the throttled prune.
- `api/routes.py` schedules it as a tracked background task (`_schedule_signal(coro)`, a task
  set like `_stream_persist_tasks`), so a failed attempt's next fallback is never delayed by a DB
  write. `_drain_stream_persist_tasks()` also drains the signal task set, keeping the existing
  shutdown hook in `app.py` as the single drain point.
- A small helper inside `_handle()`, `_emit_signal(target, clock, outcome, status, *, streamed,
  tracker=None, usage=None)`, builds the row and is called from:
  - `_note_attempt_failure` (outcome `error`). The hook gains a keyword `status: int | None`
    argument that callers pass explicitly (null for network exceptions). The clock is the
    current attempt's clock.
  - each `_log_error_and_raise` site reached from an upstream response (`client_error`).
  - each non-stream success path (`ok`).
  - each streaming `finally` block: `ok` when the stream completed, `aborted` on client abort,
    `error` on upstream failure.

### 4. Aggregation: `storage/model_signals.py`

`async def get_model_signals(db_path, *, window_days: int = 7, max_rows: int = 50_000)
-> ModelSignals`

- Reads the most recent `max_rows` rows inside the window (bounded scan, newest first).
- Returns two groupings, each a list of `SignalStats` dataclasses:
  - `by_model` keyed by `model`
  - `by_account` keyed by `(model, account_id)`
- `SignalStats` fields: `samples` (ok + error), `errors`, `error_rate` (`errors / samples`, null
  when `samples == 0`), `ttft_p50_ms`, `ttft_p90_ms`, `tps_p50`, `tps_p90` (stream rows only),
  `nonstream_tps_p50`, `last_seen` (ISO timestamp), `sufficient` (`samples >= 5`).
- Percentiles computed in Python (nearest-rank) over the fetched values.
- Cache: module-level, keyed by DB path, 60 s TTL; `invalidate_model_signals_cache()` is called
  from `reload_providers`. Concurrent misses coalesce on one in-flight task.

Nothing consumes it in this part except tests; #183 and #187 part 2 will.

## Error handling

Signal recording and aggregation are fail-safe: any exception is logged at warning level and
the request proceeds unchanged. Aggregation errors return an empty `ModelSignals`.

## Testing

- Unit `tests/unit/streaming/`: tracker sets `first_content_at` on the first content event only,
  ignores `MessageStart`/empty text, keeps the first timestamp.
- Unit `tests/unit/storage/test_attempt_signals.py`: insert/read, prune by age and throttle,
  exception swallowing.
- Unit `tests/unit/storage/test_model_signals.py`: percentiles, error rate excludes
  `client_error`/`aborted`, stream vs non-stream TPS split, `sufficient` threshold, window and
  `max_rows` bounds, cache TTL and invalidation.
- Unit: `compute_output_tps` edge cases (zero tokens, sub-50 ms, normal).
- Integration (ASGI + respx): fallback A (500) → B (200) yields one `error` row for A and one
  `ok` row for B; a streamed success records `ttft_ms` and `output_tps`; a non-eligible 400
  yields `client_error`; a client abort yields `aborted`.
- `scripts/migration_smoke.py` passes against legacy DBs (new table only, no column changes).

## Acceptance

- [ ] `attempt_signals` written for every upstream attempt on all request paths
- [ ] TTFT measured from attempt dispatch to first content event, per attempt
- [ ] Error rate per account counts fallback-masked failures; client errors and aborts excluded
- [ ] `get_model_signals()` with 60 s cache invalidated on provider reload
- [ ] 7-day throttled retention prune
- [ ] No routing or dashboard behavior change; all CI gates green
