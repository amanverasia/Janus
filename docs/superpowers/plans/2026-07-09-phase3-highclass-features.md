# Phase 3 — 9router High-Class Features & Routing Fixes

> Date: 2026-07-09
> Branch: `feat/9router-highclass`
> Source analysis: 9ROUTER-ANALYSIS.md + two deep audits (9router feature map, Janus current-state audit)

## Context

Janus already has (post PR #45): fill_first/round_robin/sticky_rr strategies, per-model
compound-key cooldowns, exponential backoff (base 2s, max 300s, 15 levels), capability-aware
combo reordering, savers+usage in all 3 upstream paths, 8 OAuth/subscription providers,
fixed-UTC quota windows, non-fallback 4xx request logging.

Remaining gaps vs 9router, ordered by value:

## Global constraints

- Python 3.12+, FastAPI, pydantic. Style: ruff (line length 100), mypy strict-ish (no new errors).
- All savers are FAIL-OPEN: any exception inside a saver/pipeline stage must log and continue.
- Rotation state read-modify-write in `FallbackHandler.resolve_attempts` must remain
  synchronous (no `await` between read and write) — this is the documented lock-free invariant.
- Every task ships unit tests; integration tests where routing behavior changes.
- Gate per task: `.venv/bin/python -m pytest <relevant tests> -q`, `ruff check src tests`,
  `ruff format`, `mypy src/janus` — zero errors.
- Settings live in the SQLite `settings` table with defaults in
  `src/janus/storage/settings.py` (`SAVER_SETTING_DEFAULTS` / `SERVER_SETTING_DEFAULTS`);
  resolvers are pure functions over the settings dict.

---

## Task 1 — Sticky combo rotation (fix dead `combo_sticky_limit` parameter)

**Files:** `src/janus/routing/fallback.py`, `tests/unit/routing/test_fallback_strategies.py` (or nearest existing fallback test file)

`resolve_attempts()` accepts `combo_sticky_limit: int = 1` but never uses it — only
`combo_strategy == "round_robin"` is acted on, advancing every request. 9router stays on a
combo model for N consecutive requests (`comboStickyRoundRobinLimit`, default 1).

**Change:** In the combo branch of `resolve_attempts`, when `combo_strategy == "round_robin"`
and `combo_sticky_limit > 1`, only advance `self._combo_rotation[model_str]` every
`combo_sticky_limit` requests. Implementation: keep a per-combo use counter
`self._combo_sticky: dict[str, int]`; increment per request; advance index when counter
reaches the limit, then reset counter. `combo_sticky_limit=1` must behave exactly as today
(advance every request). Include `_combo_sticky` in `adopt_runtime_state`.

**Tests:** with limit=3 and combo [a,b]: requests 1-3 order starts with a, 4-6 with b, 7-9
with a. With limit=1: alternates each request (existing behavior — add regression assert).

## Task 2 — 503 + Retry-After when all accounts cooled down; body-text error classification

**Files:** `src/janus/routing/fallback.py`, `src/janus/routing/errors.py`,
`src/janus/api/routes.py`, tests in `tests/unit/routing/` + one integration test.

**2a.** Today `resolve_attempts` raises `ValueError("No available providers …")` and
`routes.py:372` maps every ValueError to HTTP 400. An unknown model IS a 400, but "all
accounts cooled down" should be **503 with a Retry-After header** (9router returns
`allRateLimited` + earliest lock expiry).

- Add `class AllAccountsCooledDown(Exception)` in `fallback.py` carrying `retry_after: float`
  (earliest cooldown expiry minus now, min 1.0). Add helper
  `earliest_cooldown_expiry(account_ids, model) -> float | None` reading `self._cooldowns`
  (consider both `__all__` and model-specific keys).
- Raise it (instead of ValueError) in both the combo and single-model "no available" branches.
- In `routes.py`, catch it and raise
  `HTTPException(503, detail="All accounts for '<model>' are cooling down; retry after Ns", headers={"Retry-After": str(int(retry_after))})`.
  Unknown model stays ValueError → 400.

**2b.** 9router classifies by error-body TEXT before status: "rate limit", "too many
requests", "quota exceeded", "capacity", "overloaded" → rate_limit backoff regardless of
status. Janus classifies by status only, so a 400/200-wrapped "quota exceeded" doesn't cool
down or fall back.

- In `errors.py` add
  `refine_error_type(status_code: int, body: dict | None) -> ErrorType`: start from
  `classify_error(status_code)`; if body text (serialize `body.get("error")` best-effort,
  lowercase, first 2000 chars) contains any of
  `("rate limit", "too many requests", "quota exceeded", "capacity", "overloaded", "resource exhausted")`
  → return `RATE_LIMIT`. Add `is_fallback_eligible_refined(status, body)` that returns True
  when the refined type is RATE_LIMIT even if the status alone wasn't eligible.
- In `routes.py`, at each non-stream error-handling site that currently does
  `classify_error(result.status_code)` / `is_fallback_eligible(result.status_code)`, use the
  refined variants passing `result.json_data`. Streaming paths unchanged (can't retry
  mid-stream).

**Tests:** unit — refine on 400+"Rate limit exceeded" → RATE_LIMIT & eligible; 400 plain →
CLIENT_ERROR & not eligible; 429 stays RATE_LIMIT. Integration — provider returns 400 with
"quota exceeded" body → second account is tried; all accounts cooled → 503 with Retry-After.

## Task 3 — Fusion combo strategy (parallel panel + judge synthesis)

**Files:** new `src/janus/routing/fusion.py`, `src/janus/api/routes.py`,
`src/janus/storage/settings.py`, tests `tests/unit/routing/test_fusion.py` +
`tests/integration/test_fusion.py`.

Port of 9router `handleFusionChat` (open-sse/services/combo.js), adapted to Janus's
canonical model. When a combo's strategy is `fusion`:

1. Panel = the combo's models. 0 models → 400. 1 model → fall through to normal single-model
   handling (return None from the fusion helper; routes falls back to sequential path with
   just that model).
2. Build panel request from the post-saver `CanonicalRequest`: strip `tools`/`tool_choice`,
   force non-streaming. Flatten tool history: ToolResult parts → assistant-visible text
   `"[Tool result: <text…500 chars>]"`, assistant tool_use parts → `"[Called tools: <names>]"`
   (helper `flatten_tool_history(req) -> CanonicalRequest`).
3. Fan out with `asyncio.create_task` per panel model, each attempt going through the normal
   single-model attempt machinery refactored into a reusable coroutine — simplest viable:
   call the provider directly via `handler.resolve_attempts(m)` → first available target →
   `provider.call(...)` non-streaming, one attempt per panel model (no intra-panel fallback).
   Wrap each in `asyncio.wait_for` with `fusion_hard_timeout_s`.
4. Quorum-grace collection: as soon as `min_panel` (default 2, clamped to panel size)
   succeed, wait `straggler_grace_s` (default 8.0) more, then cancel stragglers. Hard cap
   `hard_timeout_s` (default 90.0).
5. Judge = `combo_fusion_judge` setting if set (a `prefix/model` string), else panel[0].
   Build judge prompt exactly in 9router's spirit: original conversation + appended user turn
   containing anonymized `[Source 1..N]` answers + instruction to synthesize one
   authoritative answer analyzing consensus/contradictions/coverage without revealing
   multiple models were used. Judge request keeps the client's original `stream` flag and
   tools. Route the judge through the NORMAL `_handle` attempt loop (reuse existing
   machinery) by rewriting `canonical_req` — i.e. fusion helper returns the judge-ready
   CanonicalRequest + judge model; routes.py continues the standard flow with it.
6. Partial failures: skip failed/empty/timed-out panel answers. 0 answers → HTTPException
   503. Exactly 1 → skip judge; continue standard flow with the original request pinned to
   that answering model (simplest: return that single panel answer's model as "judge" with
   the ORIGINAL request unchanged).

**Settings** (SERVER_SETTING_DEFAULTS): `combo_fusion_min_panel` = "2",
`combo_fusion_straggler_grace_s` = "8", `combo_fusion_hard_timeout_s` = "90",
`combo_fusion_judge` = "" + resolvers with int/float guards (follow `resolve_sticky_limit`
pattern). `resolve_combo_strategy` gains "fusion" as a valid value (validate: fallback |
round_robin | fusion, default fallback on garbage).

**routes.py wiring:** after `combo_strat` is resolved and BEFORE `resolve_attempts`: if
`combo_strat == "fusion"` and `handler.registry.lookup_combo(canonical_req.model)` is not
None with ≥2 models → run fusion panel; replace `canonical_req` with the judge request and
`canonical_req.model` with the judge model, then proceed through the existing attempt loop
untouched (fallback, passthrough, streaming, usage recording all reuse existing code).
Record panel usage: call `record_usage` per successful panel answer with its real token
counts, status 200, and the panel model.

**Tests:** unit — flatten_tool_history shapes; judge prompt contains [Source N] and not
provider names; quorum logic with fake coroutines (2-of-3 then grace; all-fail → 503;
1-of-3 → no judge). Integration — combo of two respx-mocked openai providers + fusion
strategy: both get called, judge (panel[0]) receives synthesis prompt, client gets judge
answer.

## Task 4 — Per-saver savings metrics (measure, log, dashboard)

**Files:** `src/janus/tokensavers/pipeline.py`, `src/janus/tokensavers/base.py` (if stats
type lives there), `src/janus/dashboard/routes.py`, `src/janus/dashboard/templates/savers_list.html`,
tests `tests/unit/tokensavers/test_pipeline_metrics.py`.

9router logs `[RTK] saved <bytes>/<bytes> (<pct>%)` per request; neither project persists
per-saver savings. Janus should measure and keep cumulative in-memory counters + log line.

- Add `SaverStats` dataclass: `{name: str, bytes_before: int, bytes_after: int}` and a
  module-level size probe `request_size(req: CanonicalRequest) -> int` (len of
  `req.model_dump_json(include={"messages", "system"})` — messages+system only, cheap).
- In `SaverPipeline.apply` / `apply_async`: measure size before/after each saver; when a
  saver shrinks the request, `logger.info("[%s] saved %d / %d bytes (%.1f%%)", …)`; always
  accumulate into `self.stats: dict[str, dict[str, int]]`
  (`{saver_name: {"requests": n, "bytes_before": b, "bytes_after": a}}`). Prompt-injecting
  savers (Caveman/Ponytail) will show negative savings — clamp displayed savings at ≥0 in the
  UI but store真 raw sums. Measurement failures must never break the pipeline (fail-open).
- Pipeline is rebuilt on reload (`reload_savers`) — carry stats over: add
  `adopt_stats(other: SaverPipeline)` mirroring `FallbackHandler.adopt_runtime_state`, call
  it in `dashboard/reload.py`.
- Dashboard savers page: under each enabled saver card show
  `saved X KB across N requests (Y% avg)` from `app.state.saver_pipeline.stats`.
  (In-memory, resets on restart — label it "since restart".)

**Tests:** RTK on a compressible tool result → stats show bytes_after < bytes_before and one
request counted; a saver that raises → stats unchanged for it, pipeline continues; adopt_stats
carries counters.

## Task 5 — RTK filter upgrade (port 9router's detector/filter set)

**Files:** `src/janus/tokensavers/rtk.py`, tests `tests/unit/tokensavers/test_rtk.py` (extend).

Janus RTK has 4 rules (ansi, git-diff mode-lines, ls-permissions, log-dedup) + head-only
8000-char truncate. 9router has 12 Rust-ported filters with priority detection and caps.
Port the high-value subset, keeping Janus's pure-function style:

- Constants (from 9router `constants.js`): `MIN_COMPRESS_SIZE = 500` (skip smaller — replaces
  current 50), `RAW_CAP = 10 * 1024 * 1024` (pass through larger untouched),
  `DETECT_WINDOW = 1024`, `GIT_DIFF_HUNK_MAX_LINES = 100`, `GIT_LOG_MAX_LINES = 200`,
  `GREP_PER_FILE_MAX = 10`, `FIND_PER_DIR_MAX = 10`, `TREE_MAX_LINES = 200`,
  `STATUS_MAX_FILES = 10`, `SMART_TRUNCATE_HEAD = 120`, `SMART_TRUNCATE_TAIL = 60`,
  `SMART_TRUNCATE_MIN_LINES = 250`.
- New filters (each `def f(text: str) -> str`, must never return empty and never grow input —
  keep the existing `if len(result) >= len(text): return text` guard everywhere):
  - `compress_git_log`: keep first `GIT_LOG_MAX_LINES` lines of `commit …` blocks, collapse
    bodies to first line of each commit message beyond 20 commits.
  - `compress_git_status`: cap modified/untracked lists at `STATUS_MAX_FILES` each with
    `… (+N more)` markers.
  - `compress_grep_output`: group `path:line:` matches by file, keep `GREP_PER_FILE_MAX`
    per file with `… (+N more in file)` markers.
  - `compress_find_output`: group by parent dir, keep `FIND_PER_DIR_MAX` per dir.
  - `compress_tree_output`: keep first `TREE_MAX_LINES` lines + summary line.
  - `compress_build_output`: keep lines matching `error|warning|failed|FAILED|✗|✘` + 3
    context lines around each, plus last 30 lines; only if it shrinks.
- `smart_truncate` becomes **line-based head+tail**: if > `SMART_TRUNCATE_MIN_LINES` lines,
  keep first `SMART_TRUNCATE_HEAD` + last `SMART_TRUNCATE_TAIL` lines with
  `\n[… N lines truncated …]\n` marker. Keep the old 8000-char fallback for single-line blobs.
- `_detect_and_compress` gains priority detection over the first `DETECT_WINDOW` chars:
  git-log (`^commit [0-9a-f]{7,40}`) → git-diff → git-status (`^(M|A|D|R|\?\?)\s` porcelain or
  "Changes not staged") → build-output (`error\[|warning:|FAILED|BUILD`) → grep
  (`^\S+:\d+[:\s]` on ≥5 lines) → find (≥10 path-only lines) → tree (`├──|└──`) → ls →
  dedup-log → smart_truncate (always last). First match wins, then smart_truncate applies on
  top. Gate everything on `MIN_COMPRESS_SIZE`/`RAW_CAP`.

**Tests:** one focused test per filter (synthetic input → shrinks, markers present, never
empty), detection priority (git log vs diff), never-grow property, MIN_COMPRESS_SIZE gate.

## Task 6 — Caveman levels (lite/full/ultra) + dashboard select

**Files:** `src/janus/tokensavers/caveman.py`, `src/janus/storage/settings.py`,
`src/janus/dashboard/reload.py`, `src/janus/dashboard/routes.py` (saver settings POST),
`src/janus/dashboard/templates/savers_list.html`, tests.

Mirror the existing Ponytail pattern exactly (`PonytailSaver(level=…)`,
`saver_ponytail_level` setting, dashboard `<select>`):

- `CavemanSaver(level: str = "full")`, `PROMPTS` dict:
  - `lite`: brevity, skip pleasantries, keep code/errors/URLs exact.
  - `full`: current prompt + safety boundaries from 9router — "write normally for security
    warnings, irreversible-action confirmations, and multi-step instructions; preserve the
    user's language; never abbreviate code, paths, commands, error messages, or URLs".
  - `ultra`: telegraphic register — articles/fillers dropped, sentence fragments allowed,
    same safety boundaries verbatim.
- `SAVER_SETTING_DEFAULTS["saver_caveman_level"] = "full"`; `reload_savers` passes it;
  invalid level in DB → fall back to "full" (guarded, not raised).
- Dashboard: level `<select>` on the caveman card (copy the ponytail markup/handler).

**Tests:** each level injects its prompt; invalid level from settings falls back to full
(reload path); ponytail regression untouched.

## Task 7 — Combo strategy dashboard UI

**Files:** `src/janus/dashboard/routes.py`, `src/janus/dashboard/templates/combos.html`
(or settings.html — follow where account strategy lives), integration test in
`tests/integration/test_dashboard_crud.py` style.

Backend supports `combo_strategy` / `combo_sticky_limit` / (after Task 3)
`combo_fusion_judge` + fusion tuning, but no UI exists.

- Add a "Combo routing" section to the Settings page mirroring the account-strategy block:
  strategy `<select>` (fallback / round_robin / fusion), sticky-limit number input,
  fusion judge text input (placeholder `prefix/model`, only relevant for fusion),
  min-panel/grace/timeout inputs (small, grouped, with defaults shown).
- POST handler persists via `set_setting`; values validated server-side (ints/floats guarded,
  strategy whitelisted) — invalid input re-renders with current values (follow existing
  settings POST pattern).

**Tests:** GET settings page contains the combo section; POST updates settings rows; invalid
strategy value is rejected/ignored.

## Task 8 — Per-provider model allowlist (selective models)

**Files:** `src/janus/config/schema.py`, `src/janus/storage/database.py`,
`src/janus/storage/providers_db.py`, `src/janus/routing/upstream_expand.py` (and any other
row→ProviderConfig builder), `src/janus/providers/registry.py`, `src/janus/api/routes.py`
(models listing), `src/janus/dashboard/routes.py` + provider form template, tests.

**User story:** "I have the Anthropic provider added; they provide many Claude models, but I
only want users to use claude-opus-4-7 — no other model." Today `registry.lookup()` routes
ANY `prefix/model` string to the provider; the `models` list is display-only for
`/v1/models`. There is no enforcement point.

**Design:** new optional `allowed_models: list[str]` on `ProviderConfig` (default empty =
no restriction — current behavior unchanged). Patterns support `fnmatch` globs
(`claude-opus-*`). Enforcement lives in `ProviderRegistry.lookup()`.

- `schema.py`: `allowed_models: list[str] = Field(default_factory=list)` on ProviderConfig.
- DB: idempotent migration adding `allowed_models TEXT NOT NULL DEFAULT '[]'` to
  `providers` (follow the existing PRAGMA table_info migration pattern for added columns,
  e.g. quota/transport columns). `providers_db.py` create/update persists it (JSON list, same
  as `models`). `seed_from_config` includes it.
- Row→config builders (`upstream_expand.py` and any sibling that constructs ProviderConfig
  from a DB row): parse the JSON column into `allowed_models`.
- `registry.py`: add module-level
  `model_allowed(model: str, allowed: list[str]) -> bool` (True when list empty; else exact
  match or `fnmatch.fnmatchcase`). In `lookup()`, skip configs where
  `not model_allowed(rest, config.allowed_models)`. If every config for the prefix is
  skipped, return the empty-filtered result: `lookup` returns `None` when no config passes
  (so routes yield the existing 400 "Unknown model" path — check what `resolve_attempts`
  does with `None` vs `[]` and keep the unknown-model ValueError semantics).
- `/v1/models` listing (routes.py `list_models`): only list models that pass
  `model_allowed` for at least one config of the prefix, so restricted models disappear
  from discovery too.
- Dashboard provider form: "Allowed models" text input (comma-separated, placeholder
  "empty = all models"), persisted through the create/update handlers, shown in the
  provider edit view. Follow the existing `models` field markup/parsing exactly.
- Combos: no special handling — a combo member blocked by allowlist simply resolves to no
  targets and is skipped (verify a combo with one blocked + one allowed member still works;
  add a test).

**Tests:** unit (registry) — empty allowlist routes anything; exact allowlist blocks others;
glob `claude-opus-*` matches `claude-opus-4-7` and not `claude-sonnet-4-5`; multi-account
same prefix with different allowlists → only permitted configs returned. Integration —
provider with `allowed_models=["m-allowed"]`: request for `prefix/m-blocked` → 400,
`prefix/m-allowed` → 200; `/v1/models` hides blocked models; DB migration round-trip
(create provider with allowlist via providers_db, reload, enforced).

---

## Deferred (recorded in todo.md, not this phase)

- Sticky/rotation counter persistence across restarts (9router persists
  `consecutiveUseCount`/`lastUsedAt` per connection; Janus in-memory-only, survives reload
  via adopt_runtime_state). Low value: worst case after restart is one extra rotation step.
- Rolling (vs fixed UTC) quota windows — needs per-window anchor timestamps.
- Wenyan caveman tiers (classical Chinese) — niche.
- `_passthrough_call` public provider interface (works today, getattr-guarded).
