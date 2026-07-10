# Pricing Sync — Live model pricing from LiteLLM/OpenRouter

> Date: 2026-07-10
> Branch: `feat/pricing-sync`
> Problem: `pricing/builtin.py` is a frozen ~40-model table; on the production VPS 825/2418
> usage rows (34%) with real tokens recorded $0.00 cost (grok-4.5, claude-opus-4.8, glm-5.2,
> qwen3.7-max, kimi-k2.5 all unpriced). Budgets enforce against undercounted spend.

## Global constraints

- Python 3.12, FastAPI, aiosqlite; ruff (line 100) + mypy clean; every task ships tests.
- Sync must be FAIL-OPEN: fetch failure logs a warning and keeps the last-synced catalog;
  never blocks startup or requests. All network calls have explicit timeouts (30s).
- Resolution precedence in PricingRegistry: user override > synced catalog > builtin.
  Existing prefix-fallback matching applies within each layer (override exact > catalog
  exact/prefix > builtin exact/prefix is NOT required — simpler: overrides dict, then
  catalog dict, then builtin dict merged in reverse-precedence order into one table the
  way BUILTIN + overrides merge today, then existing _match logic runs once).
- Units: store per-MTok floats (matching ModelPricing). LiteLLM gives per-token cost
  (multiply by 1e6); OpenRouter gives per-token strings (parse float, multiply by 1e6).
- Subscription providers (copilot/, kiro/, cursor/, claude_oauth, antigravity, mimo_free,
  opencode_free prefixes) must NOT be priced by the catalog — their marginal cost is $0
  by design. compute_cost already returns 0 for unknown models; do not add catalog rows
  that would accidentally match them (catalog keys are bare model ids from the sources,
  which is fine — no special handling needed beyond not inventing entries).

## Task 1 — Catalog storage + fetchers + sync orchestrator

**Files:** new `src/janus/pricing/sync.py`, new `src/janus/storage/pricing_catalog.py`,
`src/janus/storage/database.py` (schema), tests `tests/unit/pricing/test_sync.py`.

- Schema (database.py, add to _SCHEMA):
  ```sql
  CREATE TABLE IF NOT EXISTS pricing_catalog (
      model TEXT PRIMARY KEY,
      input_per_mtok REAL NOT NULL,
      output_per_mtok REAL NOT NULL,
      cache_creation_per_mtok REAL NOT NULL DEFAULT 0,
      cache_read_per_mtok REAL NOT NULL DEFAULT 0,
      source TEXT NOT NULL,             -- 'litellm' | 'openrouter'
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
  ```
  Plus a `pricing_sync_meta` approach: reuse the `settings` table with keys
  `pricing_last_sync_at` (ISO ts) and `pricing_catalog_count` — no new meta table.
- `storage/pricing_catalog.py`: `replace_catalog(db_path, rows: list[dict]) -> int`
  (DELETE + executemany INSERT inside one transaction), `get_catalog(db_path) ->
  dict[str, dict[str, float]]` (model -> rates, same shape as get_pricing_overrides),
  `catalog_count(db_path) -> int`.
- `pricing/sync.py`:
  - `LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"`
  - `OPENROUTER_URL = "https://openrouter.ai/api/v1/models"`
  - `parse_litellm(data: dict) -> dict[str, ModelPricing]`: skip the `sample_spec` key;
    keep entries with numeric `input_cost_per_token` or `output_cost_per_token` and
    `mode` in (None, "chat", "responses", "completion") — skip embedding/image/audio/
    rerank modes. Key = the raw key AND, when the key contains "/", also the bare suffix
    after the last "/" (first writer wins for the bare id — iterate in sorted-key order
    for determinism). cache fields: `cache_creation_input_token_cost`,
    `cache_read_input_token_cost` (default 0.0). Multiply all by 1e6.
  - `parse_openrouter(data: dict) -> dict[str, ModelPricing]`: entries under `data`;
    pricing fields `prompt`/`completion`/`input_cache_read`/`input_cache_write` are
    per-token strings; skip entries where both prompt and completion parse to 0 (free
    variants) or fail to parse. Same dual-key (full id + bare suffix) rule.
  - `merge_sources(litellm, openrouter) -> dict[str, ModelPricing]`: litellm wins on
    key collision (broader, faster-updated); openrouter fills gaps.
  - `async fetch_and_sync(db_path) -> int`: httpx.AsyncClient(timeout=30); fetch both
    (each independently try/except — one source failing does not abort the other; both
    failing raises PricingSyncError), parse, merge, `replace_catalog`, set
    `pricing_last_sync_at`/`pricing_catalog_count` settings, return row count. Never
    replaces the catalog with an empty dict (guard: if merged is empty, raise instead).
- Tests: parser unit tests with small fixture dicts (litellm shape incl. sample_spec skip,
  embedding-mode skip, cache fields, 1e6 scaling; openrouter string parsing, free-model
  skip); merge precedence; fetch_and_sync with respx-mocked URLs writes rows + settings;
  one-source-down still syncs; both-down raises and leaves existing catalog untouched;
  empty-merge guard.

## Task 2 — Registry layering + reload + startup/scheduler wiring

**Files:** `src/janus/pricing/registry.py`, `src/janus/dashboard/reload.py`,
`src/janus/app.py`, new `src/janus/pricing/scheduler.py`, tests.

- `PricingRegistry.__init__(user_overrides, catalog: dict[str, dict[str, float]] | None = None)`:
  table = {**BUILTIN_PRICING, **catalog_as_ModelPricing, **overrides_as_ModelPricing}.
  Existing `get`/`_match`/`get_all` unchanged. Add `source_of(model) -> str | None`
  returning "override" | "catalog" | "builtin" for dashboard display (track three dicts
  or a parallel source map built at init).
- `reload_pricing(app)`: load overrides (existing) + `get_catalog(db_path)`, construct
  registry with both.
- `app.py` lifespan: after `reload_pricing`, if catalog is empty OR `pricing_last_sync_at`
  is older than `PRICING_SYNC_INTERVAL_HOURS` (env, default 24), kick off
  `asyncio.create_task(_initial_sync(app))` — a helper that runs `fetch_and_sync` then
  `reload_pricing(app)`, entirely try/except-logged (fail-open, non-blocking startup).
- `pricing/scheduler.py`: mirror `inventory/scheduler.py` — `run_pricing_scheduler(app,
  stop_event)` loops every `PRICING_SYNC_INTERVAL_HOURS` (env, default 24.0), calls
  `fetch_and_sync` + `reload_pricing`; `pricing_scheduler_enabled()` env toggle
  `PRICING_SYNC_ENABLED` (default true). Wire start/stop in app.py lifespan exactly like
  the inventory scheduler (create task after startup, set stop event + await on shutdown).
- Tests: registry precedence (override beats catalog beats builtin, prefix match still
  works, source_of correct); reload_pricing picks up catalog rows (tmp DB); scheduler
  respects stop event promptly (short interval via env monkeypatch).

## Task 3 — Dashboard pricing tab: sync status, sync-now, unpriced models

**Files:** `src/janus/dashboard/routes.py`, `src/janus/dashboard/templates/pricing.html`,
`src/janus/storage/analytics.py` or new query in `storage/usage.py`, tests.

- New query `get_unpriced_models(db_path, days=30) -> list[dict]`: models from `usage`
  where SUM(cost)=0 AND SUM(input_tokens+output_tokens)>0 within N days, with request
  counts and token sums, ordered by tokens DESC. Exclude models that ARE priced by the
  current registry at render time (pass registry in, filter in Python — the query returns
  candidates, the handler drops ones `registry.get(m)` resolves; a model can have old $0
  rows from before a sync).
- Pricing page context: `last_sync` (setting), `catalog_count` (setting), `unpriced`
  list, and per-row `source` badge (override/catalog/builtin) using `source_of`.
- `POST /dashboard/api/pricing/sync` handler: runs `fetch_and_sync` + `reload_pricing`,
  returns JSON {count, synced_at} or 502 with the error message; template gets a
  "Sync now" button (htmx post + result flash, follow existing button patterns) and an
  "Unpriced models seen recently" table with a per-row "Add override" shortcut that
  pre-fills the existing override form (JS prefill, copy existing edit-prefill pattern).
- Show sync freshness: "Catalog: N models, synced X ago" line; amber note when never
  synced or >48h stale.
- Tests: page renders sync status + unpriced table (seed usage rows with cost=0);
  sync endpoint with respx-mocked sources updates catalog + settings and page reflects
  count; sync endpoint failure returns 502 and page still renders.

## Task 4 — Backfill command + CLI sync

**Files:** `src/janus/cli.py`, new function in `src/janus/storage/usage.py`, tests.

- `storage/usage.py`: `backfill_costs(db_path, registry) -> tuple[int, float]`:
  iterate usage rows where (cost IS NULL OR cost=0) AND (input_tokens>0 OR
  output_tokens>0); recompute via compute_cost with stored token counts (build Usage
  from input/output/cache_creation/cache_read columns — check actual column names);
  UPDATE only rows where new cost > 0. Return (rows_updated, total_cost_added).
  Batch in one transaction.
- CLI: `janus pricing sync` (runs fetch_and_sync against the configured DB, prints count),
  `janus pricing backfill [--days N] [--dry-run]` (prints rows/total added; dry-run
  computes without writing). Follow existing cli.py asyncio.run + typer patterns and the
  existing `pricing_app` subcommand group.
- IMPORTANT: budgets count today's spend from usage.cost — backfilling today's rows will
  raise today's measured spend (that is the point; note it in the command output:
  "Note: budget enforcement uses these costs; today's spend increased by $X").
- Tests: backfill updates only zero-cost rows with tokens, leaves priced rows and
  zero-token rows untouched, dry-run writes nothing, returns correct totals.

## Explicitly out of scope

- Per-provider price overrides (reseller discounts) beyond the existing per-model overrides.
- Pricing subscription/OAuth providers ("subscription-covered" labeling — follow-up).
- Historical price versioning (catalog stores current price only; backfill uses current).
