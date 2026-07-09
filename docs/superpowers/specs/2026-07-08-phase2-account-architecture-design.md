# Phase 2 — Account Architecture Design

> **Date:** 2026-07-08
> **Status:** Design — approved scope, proceeding to plan
> **Part of:** 4-phase 9router-parity program (Phase 1 fidelity shipped; this is Phase 2)

## Goal

Make multi-account routing resilient the way 9router is: cool down the specific
(account, model) that failed rather than the whole account, back off
exponentially under rate limits (honoring `Retry-After`), let the operator pick a
selection strategy, and route multimodal combo requests to models that can
actually handle them. All in service of agents (Claude Code, Codex, Cursor, Cline,
Pi, opencode) working seamlessly across many providers/keys.

## Reference mechanics (from 9router source, corrected)

- Backoff: `base=2000ms · 2^(level-1)`, cap **300s (5min)**, `maxLevel=15`. Only
  rate-limit family backs off; others fixed. Success resets level to 0.
- `Retry-After` / `resets_at` overrides computed backoff, capped at 30min.
- Per-(account,model) locks (`modelLock_<model>`) with an account-wide `__all`
  fallback; success clears only the succeeded model's lock (+ expired ones),
  resetting account state only when no other locks remain.
- Sticky round-robin: stay on an account while `consecutiveUseCount < stickyLimit`
  (default 3), else rotate to least-recently-used; tracked via `lastUsedAt` +
  `consecutiveUseCount`.
- Selection is guarded by a promise-chain mutex (selection only, released before
  the upstream call).
- Capability reorder: detect vision/pdf/audio from the trailing user turn; stable
  tiered sort (tier 0 = all hard+soft caps, tier 1 = all hard, tier 2 = rest);
  never drops a model.

## Constraints (do not regress)

Existing contracts locked by tests — every change is additive/backward-compatible:
- `tests/unit/routing/test_resolver.py` — `resolve_attempts` ordering, rotation,
  cooldown filtering, `retry_after` override, persistence round-trip.
- `tests/unit/routing/test_rate_limit_routing.py` — RPM/RPD deprioritization.
- `tests/unit/routing/test_quota_routing.py` — window quotas keyed by `row_id`.
- `tests/unit/routing/test_errors.py` — `classify_error`/`is_fallback_eligible`.
- `tests/integration/test_stream_fallback.py` — streaming 429 rotation (Phase 1).

Key facts: `account_id = config.upstream_key_id or config.id` (bare uuid for
inventory keys). Quota keyed separately by `row_id`. `model=None`/`__all__` must
preserve today's account-level behavior so old tests pass unchanged.

---

## A. Per-model cooldowns

**Schema** (`database.py`): recreate `cooldowns` with compound PK. SQLite can't
alter a PK, so migrate table-rebuild style (create new, copy old rows as
`model='__all__'`, drop, rename), guarded by a `PRAGMA table_info` check for the
`model` column so it's idempotent.

```sql
CREATE TABLE cooldowns (
    account_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '__all__',
    expires_at REAL NOT NULL,
    error_type TEXT,
    backoff_level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, model)
);
```

**`storage/cooldowns.py`:**
- `save_cooldown(db_path, account_id, expires_at, model="__all__", error_type=None, backoff_level=0)`
  — upsert on `(account_id, model)`.
- `get_active_cooldowns(db_path) -> dict[str, tuple[float, int]]` — deletes expired,
  returns `{f"{account_id}::{model}": (expires_at, backoff_level)}`. (Return shape
  widened to carry backoff_level; `FallbackHandler.load_cooldowns` adapts.)

**`FallbackHandler`:**
- `self._cooldowns: dict[tuple[str, str], float]` keyed by `(account_id, model)`.
- `self._backoff: dict[tuple[str, str], int]` — current backoff level per key.
- `is_available(account_id, model=None)`: available unless a live entry exists for
  `(account_id, model)` OR `(account_id, "__all__")`. With `model=None`, checks
  only `__all__` (preserves current semantics; old tests pass).
- `mark_cooldown(account_id, error_type, model=None, retry_after=None, duration=None)`:
  resolves `model_key = model or "__all__"`, computes duration via backoff (B),
  updates in-memory + persists with model + level.
- `resolve_attempts(model_str, ...)`: extract `specific_model` (the part after `/`)
  and filter `is_available(t.account_id, specific_model)`.

## B. Exponential backoff + Retry-After

**`routing/errors.py`:**
```python
BACKOFF_BASE_MS = 2000
BACKOFF_MAX_S = 300.0
BACKOFF_MAX_LEVEL = 15
RETRY_AFTER_CAP_S = 1800.0  # 30 min
FIXED_COOLDOWNS = {"server_error": 30.0, "auth_error": 300.0, "network": 15.0}

def get_cooldown(error_type: str, backoff_level: int = 0) -> tuple[float, int]:
    if error_type == "rate_limit":
        new_level = min(backoff_level + 1, BACKOFF_MAX_LEVEL)
        secs = min(BACKOFF_BASE_MS * (2 ** (new_level - 1)) / 1000, BACKOFF_MAX_S)
        return secs, new_level
    return FIXED_COOLDOWNS.get(error_type, 60.0), 0
```

**`FallbackHandler.mark_cooldown`** reads the current level for `(account_id,
model_key)`, calls `get_cooldown`, stores the new level. If `retry_after` given:
`duration = min(retry_after, RETRY_AFTER_CAP_S)` and level resets to 0 (override).
`duration=` arg still wins outright (keeps `test_resolver` retry_after/duration
tests green — verify their exact expectations and adapt test values only if the
numeric cooldown constants changed for rate_limit; server/auth/network fixed
values are unchanged).

**`mark_success(account_id, model=None)`** (new): clears `(account_id, model_key)`
and `(account_id, "__all__")` cooldown + backoff entries, and best-effort deletes
the persisted rows. Called from `_handle` success paths (streaming `finally` after
a clean stream, and non-streaming after a 2xx).

**`RawResult.retry_after: float | None = None`** (new field). Each provider parses
the `Retry-After` header on a ≥400 response (seconds or HTTP-date) into
`retry_after`. `_handle` threads it into `mark_cooldown(..., retry_after=...)`.

## C. Selection strategies

**Settings** (`storage/settings.py`): add to `SERVER_SETTING_DEFAULTS`:
`server_account_strategy="round_robin"` (default preserves current behavior),
`server_sticky_limit="3"`. Add `resolve_account_strategy(settings)` +
`resolve_sticky_limit(settings)` helpers.

**`AccountStrategy` StrEnum** (`routing/fallback.py` or a small new module):
`FILL_FIRST`, `ROUND_ROBIN`, `STICKY_RR`.

**`resolve_attempts(..., strategy=ROUND_ROBIN, sticky_limit=3)`** — new kwargs with
defaults matching today. Ordering:
- `FILL_FIRST`: keep DB order (priority/credits/age) — no rotation.
- `ROUND_ROBIN`: current `_rotation_counters` behavior (unchanged default path).
- `STICKY_RR`: stay on the current head account for `sticky_limit` consecutive
  selections (tracked in-memory `self._sticky: dict[pool_key, tuple[account_id, int]]`),
  then advance the rotation index. In-memory only (Janus is single-process, single
  user) — no DB `last_used_at` needed, which keeps the change contained. Mutex not
  required for a single-user local gateway, but rotation counter mutation stays in
  the synchronous `resolve_attempts` (no await between read and write), so it's
  already race-free.

`_handle` reads strategy+limit from settings (already loads `get_all_settings`) and
passes them to `resolve_attempts`. Client-key sticky (existing) still overrides
when enabled.

## D. Capability-aware combo routing

**New `routing/capabilities.py`:**
- `detect_required_capabilities(req: CanonicalRequest) -> frozenset[str]` — scans the
  trailing user message's content parts: `ImagePart` → `vision`; (future
  `FilePart`/pdf when added). Tools named `*search*` → `search`. Only the last user
  turn (modalities of the current ask).
- `reorder_combo_by_capabilities(models: list[str], required, capabilities_of) -> list[str]`
  — stable tiered sort; tier 2 if missing a hard cap (`vision`), tier 0 if all
  caps, else tier 1. Never drops.
- `capabilities_of(prefix) -> dict[str, bool]` reads a new optional `capabilities`
  block from `catalog.py` gateway entries (add `vision`/`pdf`/`tool_use` to the
  handful of providers where it matters; default `{"tool_use": True}` when absent —
  additive, no refactor).

**Integration:** in `FallbackHandler.resolve_attempts` combo branch, if a
`CanonicalRequest` is available, reorder `combo_models` before expansion. Threading:
add optional `required_caps: frozenset[str] = frozenset()` kwarg to
`resolve_attempts`; `_handle` computes it via `detect_required_capabilities` and
passes it. Empty set → no reorder (default, old tests unaffected).

---

## Testing (TDD)

New unit tests: `test_cooldowns_per_model.py`, `test_backoff.py`,
`test_account_strategies.py`, `test_capabilities.py`. New integration:
`test_per_model_cooldown_e2e.py` (429 on model A/keyX rotates, model B/keyX still
served), `test_backoff_escalation.py` (repeated 429 → longer cooldown; success
resets). Every existing routing test must stay green unchanged (or with only
numeric-constant updates where the rate_limit cooldown value legitimately changed
from fixed-60 to backoff — document each such change).

## Non-goals

Provider-registry dataclass refactor, dashboard strategy UI, cross-process locking,
fusion combos, DB-persisted `last_used_at`. (Deferred / dropped.)

## Success criteria

- 429 on `gpt-4o` via key A cools down only that pair; `gpt-4o-mini` on key A and
  `gpt-4o` on key B still route.
- Repeated 429s on a pair escalate cooldown (2s→4s→…→cap); a success resets it.
- `Retry-After: 30` from upstream yields a ~30s cooldown, not the backoff value.
- Operator can set `fill_first`/`round_robin`/`sticky_rr` via settings.
- An image request through a mixed combo tries a vision-capable model first.
- Full suite green; ruff + mypy clean.
