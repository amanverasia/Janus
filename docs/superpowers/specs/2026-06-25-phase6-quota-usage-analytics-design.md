# Phase 6: Quota & Usage Analytics

## Overview

Add cost estimation per request using model pricing tables, rich analytics (time-series, per-dimension breakdowns, success rates), and daily budget enforcement with configurable warn/block thresholds. Janus tracks which client API key made each request and enforces per-key and global daily spend limits.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Cost storage | Hybrid (store computed cost + raw token counts) | Fast queries for budgets, raw tokens allow recompute if pricing changes |
| Pricing source | Builtin defaults + YAML override | Ships ready to use, users can customize |
| Streaming usage | Deferred | Not blocked; cost analytics reflect non-streaming only for now |
| Per-key attribution | Yes | Enables per-key budgets and spending breakdowns |
| Budget enforcement | Warn + block | Soft threshold warns, hard threshold blocks |
| Budget period | Daily | Resets at midnight |
| Cache token pricing | Separate | Cache tokens have different rates than standard input |
| Dashboard charts | Chart.js via CDN | Visual analytics without npm |

## 1. Pricing Engine

New `src/janus/pricing/` package.

### 1.1 Pricing Models (`pricing/models.py`)

```python
@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float            # $ per million input tokens
    output_per_mtok: float           # $ per million output tokens
    cache_creation_per_mtok: float   # $ per million cache-creation tokens
    cache_read_per_mtok: float       # $ per million cache-read tokens
```

All rates are in USD per million tokens. Frozen dataclass for immutability.

### 1.2 Builtin Pricing Table (`pricing/builtin.py`)

A hardcoded `dict[str, ModelPricing]` covering ~30 popular models across providers:

- **Anthropic:** claude-sonnet-4-20250514, claude-opus-4-20250514, claude-3.7-sonnet, claude-3.5-haiku, etc.
- **OpenAI:** gpt-4o, gpt-4o-mini, o3, o4-mini, gpt-4.1, etc.
- **Google:** gemini-2.0-flash, gemini-2.5-pro, gemini-1.5-pro, etc.
- **Others:** deepseek-chat, deepseek-reasoner, llama-3.3-70b, etc.

Model name keys match the model strings that providers actually return in API responses (e.g., `claude-sonnet-4-20250514`, not `claude-4-sonnet`).

### 1.3 Pricing Registry (`pricing/registry.py`)

```python
class PricingRegistry:
    def __init__(self, user_overrides: dict[str, dict[str, float]]):
        self._table: dict[str, ModelPricing] = {**BUILTIN_PRICING}
        for model, rates in user_overrides.items():
            self._table[model] = ModelPricing(**rates)

    def get(self, model: str) -> ModelPricing | None:
        ...
```

### 1.4 Cost Calculator (`pricing/calculator.py`)

Standalone pure function — takes a registry, computes the dollar cost:

```python
def compute_cost(usage: Usage, model: str, registry: PricingRegistry) -> float:
    pricing = registry.get(model)
    if pricing is None:
        return 0.0
    return (
        (usage.input_tokens / 1_000_000) * pricing.input_per_mtok
        + (usage.output_tokens / 1_000_000) * pricing.output_per_mtok
        + (usage.cache_creation_input_tokens / 1_000_000) * pricing.cache_creation_per_mtok
        + (usage.cache_read_input_tokens / 1_000_000) * pricing.cache_read_per_mtok
    )
```

- `get()` does exact match first, then tries progressively shorter prefix matches (strips `-alias`, `-latest`, dated suffixes) to handle provider-specific model variants.
- Unknown model returns `None` from `get()`, cost = `$0.0` from `compute_cost()`. Not an error.
- The registry is constructed at app startup in `app.py` from `config.pricing` and stored on `app.state.pricing_registry`.

### 1.5 Config Extension (`config/schema.py`)

```python
class JanusConfig(BaseModel):
    ...
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
```

Users override or add pricing in YAML:
```yaml
pricing:
  my-custom-model:
    input_per_mtok: 5.0
    output_per_mtok: 15.0
    cache_creation_per_mtok: 6.25
    cache_read_per_mtok: 0.5
```

The loader already filters `None` values, so the section can be absent/commented out.

## 2. Database Schema Changes

### 2.1 New Columns on `usage` Table

| Column | Type | Default | Purpose |
|---|---|---|---|
| `cost` | REAL | 0.0 | Computed dollar cost at recording time |
| `cache_creation_tokens` | INTEGER | 0 | Cache creation tokens from canonical model |
| `cache_read_tokens` | INTEGER | 0 | Cache read tokens from canonical model |
| `client_key_id` | INTEGER | NULL | FK to api_keys.id — which client key made the request |

### 2.2 New `budgets` Table

```sql
CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id      INTEGER,          -- NULL = global budget (applies to all keys)
    daily_limit REAL NOT NULL,    -- USD per day
    warn_pct    REAL DEFAULT 80,  -- warn threshold percentage
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (key_id) REFERENCES api_keys(id)
)
```

### 2.3 New Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_usage_cost_key ON usage(client_key_id, date(timestamp));
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider_id);
```

### 2.4 Migration Approach

No migration framework exists. In `init_db()`, after `CREATE TABLE IF NOT EXISTS` statements:

```python
# Idempotent column migration
cursor = await conn.execute("PRAGMA table_info(usage)")
existing_columns = {row[1] for row in await cursor.fetchall()}
for col, col_type, default in [
    ("cost", "REAL", "0.0"),
    ("cache_creation_tokens", "INTEGER", "0"),
    ("cache_read_tokens", "INTEGER", "0"),
    ("client_key_id", "INTEGER", "NULL"),
]:
    if col not in existing_columns:
        await conn.execute(f"ALTER TABLE usage ADD COLUMN {col} {col_type} DEFAULT {default}")
```

Safe to run on every startup — `PRAGMA table_info` makes it idempotent.

## 3. Usage Recording Changes

### 3.1 Updated `record_usage()` Signature

```python
async def record_usage(
    db_path: Path,
    *,
    provider_id: str,
    model: str,
    account_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    status: int,
    client_key_id: int | None = None,
    cost: float = 0.0,
) -> None
```

Backward-compatible: new params have defaults, existing callers won't break. Still fire-and-forget, fail-safe.

### 3.2 Recording Flow in `_handle()`

1. After `parse_upstream_response`, compute cost: `cost = compute_cost(canonical_resp.usage, target.model, pricing_registry)`
2. If the request was authenticated via a DB API key, pass `client_key_id` from the auth dependency
3. Call `record_usage(...)` with all fields including cache tokens, cost, and client_key_id

### 3.3 Auth Dependency Change (`api/deps.py`)

The API key verification dependency currently returns a boolean. It needs to also expose the resolved `key_id` (the DB row ID) so `_handle()` can pass it through to `record_usage()` and budget checks.

The dependency will return `key_id: int | None` (None for config-static keys or no-auth mode) alongside the existing boolean. This is threaded through the request state.

### 3.4 What Stays the Same

- Still fire-and-forget, fail-safe (exceptions caught, never break the request)
- Still non-streaming only (deferred per design decision)

## 4. Query & Aggregation Layer

New module `storage/analytics.py`:

### 4.1 `get_spend_summary(db_path, *, days=30) -> dict`

Total cost, tokens, requests for the time range, plus a daily breakdown for time-series charts.

```python
{
    "total_cost": float,
    "total_requests": int,
    "total_input_tokens": int,
    "total_output_tokens": int,
    "total_cache_creation_tokens": int,
    "total_cache_read_tokens": int,
    "daily": [{"date": "2025-06-25", "cost": float, "requests": int}, ...],
}
```

### 4.2 `get_breakdown(db_path, *, dimension, days=30) -> list[dict]`

Aggregate by `model`, `provider`, `account`, or `client_key`. Returns sorted rows with cost + token sums per dimension.

### 4.3 `get_budget_status(db_path, key_id=None) -> dict | None`

Today's spend vs limit. Returns `None` if no active budget exists for the scope.

```python
{
    "daily_limit": float,
    "today_spend": float,
    "remaining": float,
    "pct_used": float,
    "status": "ok" | "warning" | "exceeded",
    "warn_pct": float,
}
```

Status logic: `ok` when `pct_used < warn_pct`, `warning` when `warn_pct <= pct_used < 100`, `exceeded` when `pct_used >= 100`.

### 4.4 `get_success_rate(db_path, *, days=30) -> dict`

Counts of 2xx, 4xx, 5xx statuses from the `usage` table.

### 4.5 Budget CRUD (`storage/budgets.py`)

- `get_budgets(db_path) -> list[dict]` — all active budgets with current spend
- `create_or_update_budget(db_path, *, key_id, daily_limit, warn_pct) -> int` — upsert by key_id (NULL for global)
- `delete_budget(db_path, budget_id) -> bool`

All queries use the existing `get_connection()` async context manager. Budget check queries use the `idx_usage_cost_key` index.

## 5. Budget Enforcement

### 5.1 Thresholds

- **Soft threshold (warn):** When `pct_used >= warn_pct` (default 80%), the request proceeds. A `X-Janus-Budget-Warning: spending at {pct}% of daily limit` header is added to the response. Dashboard shows amber.
- **Hard threshold (block):** When `pct_used >= 100%`, the request is rejected before routing.

### 5.2 Block Response

HTTP `429 Too Many Requests` with:
- `Retry-After` header set to seconds until midnight local time
- JSON body (format-appropriate — OpenAI or Anthropic error shape):
  ```json
  {
    "error": {
      "message": "Daily budget exceeded. Spent $X.XX of $Y.YY limit. Resets at midnight.",
      "type": "budget_exceeded",
      "today_spend": 1.23,
      "daily_limit": 2.00
    }
  }
  ```

### 5.3 Enforcement Flow in `_handle()`

1. Resolve `client_key_id` from auth (may be `None` for config-static key or no-auth)
2. Check key-specific budget: `get_budget_status(db_path, key_id=client_key_id)`
3. Check global budget: `get_budget_status(db_path, key_id=None)`
4. If either has `status == "exceeded"`, return 429 immediately (before routing)
5. If either has `status == "warning"`, note it to add header after the response
6. Otherwise proceed with the request
7. After recording usage, the next request sees updated spend

### 5.4 Budget Resolution Priority

- If a key has its own budget, that budget applies to that key
- Global budget (key_id=NULL) applies to ALL keys independently of per-key budgets
- Both must allow the request — most restrictive wins
- No active budget = no enforcement, all requests pass
- Budget of `$0` = blocks all requests (intentional kill-switch)

### 5.5 Fail-Safe

If the DB is unavailable or the budget query errors, the request proceeds (same fail-safe pattern as usage recording). Enforcement never blocks requests due to infrastructure failures.

## 6. Dashboard UI

### 6.1 Chart.js Integration

Single CDN `<script>` tag in `base.html` alongside existing Tailwind + HTMX. Charts rendered from JSON data injected via Jinja2 `tojson` filter. Dark-themed chart config matching the existing palette.

### 6.2 Enhanced Overview Page (`/dashboard`)

- Existing 4 stat cards stay
- New "Today's Spend" card with budget progress bar (green/amber/red)
- New "30-Day Spend Trend" line chart

### 6.3 New Analytics Page (`/dashboard/analytics`)

- Date range selector (7d / 30d / 90d) — HTMX-driven content reload
- **Spend over time** — line chart, cost per day
- **Breakdown tabs** — toggle between By Model / By Provider / By Account / By Key via HTMX partial swap
- Each breakdown: table (dimension, requests, tokens, cost) + horizontal bar chart
- **Success rate** — donut chart (2xx / 4xx / 5xx)
- **Cache token impact** — stat showing estimated $ saved by caching

### 6.4 New Budgets Page (`/dashboard/budgets`)

- Table of existing budgets: scope (key name or Global), daily limit, warn %, today's spend, status badge
- Progress bars per budget row (green/amber/red)
- Create budget form (HTMX POST): select key (or "Global"), daily limit $, warn %
- Delete budget button (HTMX DELETE returning partial)

### 6.5 Navigation

Sidebar in `base.html` gets "Analytics" and "Budgets" items (between Usage and API Keys).

### 6.6 Enhanced Usage Page (`/dashboard/usage`)

Existing by-model table gets cost columns added.

## 7. CLI Commands

### 7.1 `janus budgets` Sub-App

- `janus budgets list` — table of all budgets with today's spend and status
- `janus budgets set --key <name|global> --daily <amount> --warn <pct>` — create or update a budget
- `janus budgets delete <id>` — remove a budget

### 7.2 `janus usage` (Enhanced)

- `janus usage stats` — now shows cost columns (today's cost, 30-day cost) alongside existing token stats
- `janus usage cost --days 30` — cost breakdown by model with $ amounts
- `janus usage by-key --days 30` — spending per client key

### 7.3 `janus pricing` Sub-App

- `janus pricing list` — all known models with $/Mtok rates (builtin + user overrides)
- `janus pricing show <model>` — detailed pricing for a specific model (input, output, cache rates)

All CLI commands follow the existing typer sub-app pattern.

## 8. Testing Strategy

### 8.1 Unit Tests

- `tests/unit/pricing/test_calculator.py` — `compute_cost()` with known prices, zero tokens, missing model, cache token math
- `tests/unit/pricing/test_registry.py` — builtin lookup, YAML override merging, unknown model returns None, prefix matching
- `tests/unit/pricing/test_builtin.py` — sanity checks on builtin data (all rates >= 0, required model names present)
- `tests/unit/storage/test_analytics.py` — `get_spend_summary`, `get_breakdown`, `get_budget_status`, `get_success_rate` with seeded data
- `tests/unit/storage/test_budgets.py` — budget CRUD operations
- `tests/unit/storage/test_usage.py` — update existing tests for new columns (cost, cache tokens, client_key_id)

### 8.2 Integration Tests

- `tests/integration/test_budget_enforcement.py` — request blocked when budget exceeded, warning header when at soft threshold, passes when no budget, fail-safe when DB unavailable
- `tests/integration/test_cost_recording.py` — verify cost is computed and stored for non-streaming requests, cache tokens persisted, client_key_id tracked
- `tests/integration/test_dashboard_analytics.py` — dashboard analytics page renders with seeded data, date range selector works, breakdown tabs swap correctly

### 8.3 Test Fixtures

- `tests/fixtures/pricing.py` — sample `ModelPricing` objects and mock pricing registries
- `tests/fixtures/usage_seed.py` — helper to seed the DB with N days of usage rows for analytics tests

All tests use existing patterns: `respx` for provider mocking, `ASGITransport` for integration, `.venv/bin/python -m pytest`.

## 9. File Map

### 9.1 New Files

```
src/janus/pricing/__init__.py
src/janus/pricing/models.py
src/janus/pricing/builtin.py
src/janus/pricing/registry.py
src/janus/pricing/calculator.py
src/janus/storage/analytics.py
src/janus/storage/budgets.py
src/janus/dashboard/templates/analytics.html
src/janus/dashboard/templates/budgets.html
src/janus/dashboard/templates/budgets_partial.html
tests/unit/pricing/__init__.py
tests/unit/pricing/test_calculator.py
tests/unit/pricing/test_registry.py
tests/unit/pricing/test_builtin.py
tests/unit/storage/test_analytics.py
tests/unit/storage/test_budgets.py
tests/fixtures/pricing.py
tests/fixtures/usage_seed.py
tests/integration/test_budget_enforcement.py
tests/integration/test_cost_recording.py
tests/integration/test_dashboard_analytics.py
```

### 9.2 Modified Files

```
src/janus/storage/database.py        — schema migration (new columns + budgets table + indexes)
src/janus/storage/usage.py           — record_usage() new params
src/janus/config/schema.py           — pricing config field
src/janus/api/routes.py              — cost computation, budget enforcement in _handle()
src/janus/api/deps.py                — expose resolved key_id
src/janus/dashboard/routes.py        — analytics + budgets pages + HTMX endpoints
src/janus/dashboard/templates/base.html — Chart.js CDN + nav items
src/janus/dashboard/templates/usage.html — cost columns
src/janus/dashboard/templates/overview.html — today's spend + trend chart
src/janus/cli.py                     — budgets + pricing sub-apps, enhanced usage
src/janus/app.py                     — wire PricingRegistry into app state
```

### 9.3 Unchanged

No changes to `canonical/`, `formats/`, `providers/`, `tokensavers/`, or `routing/`. This phase is purely additive in the pricing, storage, dashboard, and config layers.
