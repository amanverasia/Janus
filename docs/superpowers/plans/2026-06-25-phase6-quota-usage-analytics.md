# Phase 6: Quota & Usage Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cost estimation per request, rich analytics with charts, and daily budget enforcement with warn/block thresholds.

**Architecture:** A new `pricing/` package provides builtin model pricing tables overridable via YAML. Cost is computed at recording time and stored alongside raw token counts (hybrid approach). A new `budgets` table and enforcement layer in `_handle()` rejects requests when daily spend limits are hit. The dashboard gains Chart.js-powered analytics and budget management pages.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, Pydantic v2, Jinja2, HTMX, Chart.js (CDN), typer, pytest, respx

---

## Task 1: Pricing Models

**Files:**
- Create: `src/janus/pricing/__init__.py`
- Create: `src/janus/pricing/models.py`

- [ ] **Step 1: Create the pricing package init**

Create `src/janus/pricing/__init__.py` as an empty file.

- [ ] **Step 2: Create the pricing model**

Create `src/janus/pricing/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float
    output_per_mtok: float
    cache_creation_per_mtok: float
    cache_read_per_mtok: float
```

- [ ] **Step 3: Commit**

```bash
git add src/janus/pricing/
git commit -m "feat: add pricing models package"
```

---

## Task 2: Builtin Pricing Table

**Files:**
- Create: `src/janus/pricing/builtin.py`
- Test: `tests/unit/pricing/__init__.py`
- Test: `tests/unit/pricing/test_builtin.py`

- [ ] **Step 1: Create test package init**

Create `tests/unit/pricing/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/pricing/test_builtin.py`:

```python
from janus.pricing.builtin import BUILTIN_PRICING
from janus.pricing.models import ModelPricing


def test_builtin_has_popular_models():
    expected = [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "gpt-4o",
        "gpt-4o-mini",
        "gemini-2.0-flash",
    ]
    for model in expected:
        assert model in BUILTIN_PRICING, f"Missing builtin pricing for {model}"


def test_all_pricing_rates_non_negative():
    for model, pricing in BUILTIN_PRICING.items():
        assert pricing.input_per_mtok >= 0, f"{model}: negative input rate"
        assert pricing.output_per_mtok >= 0, f"{model}: negative output rate"
        assert pricing.cache_creation_per_mtok >= 0, f"{model}: negative cache_creation rate"
        assert pricing.cache_read_per_mtok >= 0, f"{model}: negative cache_read rate"


def test_pricing_is_model_pricing_instances():
    for model, pricing in BUILTIN_PRICING.items():
        assert isinstance(pricing, ModelPricing), f"{model}: not a ModelPricing instance"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_builtin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'janus.pricing.builtin'`

- [ ] **Step 4: Write builtin pricing data**

Create `src/janus/pricing/builtin.py`:

```python
from __future__ import annotations

from .models import ModelPricing

BUILTIN_PRICING: dict[str, ModelPricing] = {
    # Anthropic
    "claude-opus-4-20250514": ModelPricing(15.0, 75.0, 18.75, 1.5),
    "claude-sonnet-4-20250514": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3.7-sonnet-20250219": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3-5-sonnet-20241022": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3-5-haiku-20241022": ModelPricing(0.8, 4.0, 1.0, 0.08),
    "claude-3-opus-20240229": ModelPricing(15.0, 75.0, 18.75, 1.5),
    # OpenAI
    "gpt-4o": ModelPricing(2.5, 10.0, 0.0, 1.25),
    "gpt-4o-mini": ModelPricing(0.15, 0.6, 0.0, 0.075),
    "o3": ModelPricing(10.0, 40.0, 0.0, 0.0),
    "o4-mini": ModelPricing(1.1, 4.4, 0.0, 0.0),
    "gpt-4.1": ModelPricing(2.0, 8.0, 0.0, 0.5),
    "gpt-4.1-mini": ModelPricing(0.4, 1.6, 0.0, 0.1),
    "gpt-4.1-nano": ModelPricing(0.1, 0.4, 0.0, 0.025),
    # Google
    "gemini-2.5-pro": ModelPricing(1.25, 10.0, 0.0, 0.31),
    "gemini-2.0-flash": ModelPricing(0.1, 0.4, 0.0, 0.025),
    "gemini-2.0-flash-lite": ModelPricing(0.075, 0.3, 0.0, 0.01875),
    "gemini-1.5-pro": ModelPricing(1.25, 5.0, 0.0, 0.3125),
    "gemini-1.5-flash": ModelPricing(0.075, 0.3, 0.0, 0.01875),
    "gemini-1.5-flash-8b": ModelPricing(0.0375, 0.15, 0.0, 0.009375),
    # DeepSeek
    "deepseek-chat": ModelPricing(0.27, 1.1, 0.0, 0.07),
    "deepseek-reasoner": ModelPricing(0.55, 2.19, 0.0, 0.14),
    # Meta / others
    "llama-3.3-70b-instruct": ModelPricing(0.6, 0.6, 0.0, 0.0),
    "llama-3.1-405b-instruct": ModelPricing(3.0, 3.0, 0.0, 0.0),
    "mistral-large-2411": ModelPricing(2.0, 6.0, 0.0, 0.5),
    "qwen-max": ModelPricing(1.6, 6.4, 0.0, 0.4),
    "qwen-plus": ModelPricing(0.4, 1.2, 0.0, 0.1),
    "qwen-turbo": ModelPricing(0.05, 0.2, 0.0, 0.0125),
    "glm-4.7": ModelPricing(0.6, 2.2, 0.0, 0.0),
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_builtin.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/janus/pricing/builtin.py tests/unit/pricing/
git commit -m "feat: add builtin pricing table for popular models"
```

---

## Task 3: Pricing Registry

**Files:**
- Create: `src/janus/pricing/registry.py`
- Test: `tests/unit/pricing/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pricing/test_registry.py`:

```python
from janus.pricing.models import ModelPricing
from janus.pricing.registry import PricingRegistry


def test_exact_match():
    overrides: dict[str, dict[str, float]] = {}
    reg = PricingRegistry(overrides)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 2.5
    assert p.output_per_mtok == 10.0


def test_user_override_replaces_builtin():
    overrides = {
        "gpt-4o": {
            "input_per_mtok": 5.0,
            "output_per_mtok": 20.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 2.5,
        }
    }
    reg = PricingRegistry(overrides)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 5.0
    assert p.output_per_mtok == 20.0


def test_user_override_adds_new_model():
    overrides = {
        "my-custom-model": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides)
    p = reg.get("my-custom-model")
    assert p is not None
    assert p.input_per_mtok == 1.0


def test_unknown_model_returns_none():
    reg = PricingRegistry({})
    assert reg.get("does-not-exist") is None


def test_prefix_match_strips_date_suffix():
    reg = PricingRegistry({})
    p = reg.get("claude-sonnet-4-20250514-some-alias")
    assert p is not None
    assert p.input_per_mtok == 3.0


def test_prefix_match_strips_latest():
    reg = PricingRegistry({})
    p = reg.get("gpt-4o-latest")
    assert p is not None
    assert p.output_per_mtok == 10.0


def test_get_all_returns_merged_table():
    overrides = {
        "custom": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides)
    all_pricing = reg.get_all()
    assert "gpt-4o" in all_pricing
    assert "custom" in all_pricing
    assert isinstance(all_pricing["custom"], ModelPricing)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'janus.pricing.registry'`

- [ ] **Step 3: Write the registry implementation**

Create `src/janus/pricing/registry.py`:

```python
from __future__ import annotations

from .builtin import BUILTIN_PRICING
from .models import ModelPricing


class PricingRegistry:
    def __init__(self, user_overrides: dict[str, dict[str, float]]) -> None:
        self._table: dict[str, ModelPricing] = {**BUILTIN_PRICING}
        for model, rates in user_overrides.items():
            self._table[model] = ModelPricing(**rates)

    def get(self, model: str) -> ModelPricing | None:
        if model in self._table:
            return self._table[model]
        parts = model.split("-")
        for i in range(len(parts) - 1, 0, -1):
            candidate = "-".join(parts[:i])
            if candidate in self._table:
                return self._table[candidate]
        return None

    def get_all(self) -> dict[str, ModelPricing]:
        return dict(self._table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_registry.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/janus/pricing/registry.py tests/unit/pricing/test_registry.py
git commit -m "feat: add pricing registry with builtin + override merge"
```

---

## Task 4: Cost Calculator

**Files:**
- Create: `src/janus/pricing/calculator.py`
- Test: `tests/unit/pricing/test_calculator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/pricing/test_calculator.py`:

```python
from janus.canonical.models import Usage
from janus.pricing.calculator import compute_cost
from janus.pricing.registry import PricingRegistry


def test_basic_cost():
    overrides = {
        "test-model": {
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "cache_creation_per_mtok": 3.75,
            "cache_read_per_mtok": 0.3,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    cost = compute_cost(usage, "test-model", reg)
    assert cost == 3.0 + 7.5


def test_zero_tokens():
    reg = PricingRegistry({})
    usage = Usage()
    cost = compute_cost(usage, "gpt-4o", reg)
    assert cost == 0.0


def test_unknown_model():
    reg = PricingRegistry({})
    usage = Usage(input_tokens=1000, output_tokens=1000)
    cost = compute_cost(usage, "totally-unknown", reg)
    assert cost == 0.0


def test_cache_token_cost():
    overrides = {
        "test-model": {
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "cache_creation_per_mtok": 3.75,
            "cache_read_per_mtok": 0.3,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_input_tokens=500_000,
        cache_read_input_tokens=2_000_000,
    )
    cost = compute_cost(usage, "test-model", reg)
    assert abs(cost - (3.0 + 1.875 + 0.6)) < 0.0001


def test_partial_cache_only():
    overrides = {
        "test-model": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 1.0,
            "cache_creation_per_mtok": 1.0,
            "cache_read_per_mtok": 1.0,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(input_tokens=100, cache_read_input_tokens=200)
    cost = compute_cost(usage, "test-model", reg)
    expected = (100 / 1_000_000 * 1.0) + (200 / 1_000_000 * 1.0)
    assert abs(cost - expected) < 0.0001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_calculator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'janus.pricing.calculator'`

- [ ] **Step 3: Write the calculator implementation**

Create `src/janus/pricing/calculator.py`:

```python
from __future__ import annotations

from janus.canonical.models import Usage

from .registry import PricingRegistry


def compute_cost(usage: Usage, model: str, registry: PricingRegistry) -> float:
    pricing = registry.get(model)
    if pricing is None:
        return 0.0
    return (
        usage.input_tokens / 1_000_000 * pricing.input_per_mtok
        + usage.output_tokens / 1_000_000 * pricing.output_per_mtok
        + usage.cache_creation_input_tokens / 1_000_000 * pricing.cache_creation_per_mtok
        + usage.cache_read_input_tokens / 1_000_000 * pricing.cache_read_per_mtok
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/pricing/test_calculator.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/janus/pricing/calculator.py tests/unit/pricing/test_calculator.py
git commit -m "feat: add cost calculator with cache token pricing"
```

---

## Task 5: Config Schema — Add Pricing Field

**Files:**
- Modify: `src/janus/config/schema.py:40-45`

- [ ] **Step 1: Write the failing test**

Add to a new file `tests/unit/config/test_pricing_config.py`:

```python
from janus.config.schema import JanusConfig


def test_pricing_defaults_empty():
    cfg = JanusConfig()
    assert cfg.pricing == {}


def test_pricing_accepts_overrides():
    cfg = JanusConfig(
        pricing={
            "custom-model": {
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.5,
                "cache_read_per_mtok": 0.1,
            }
        }
    )
    assert "custom-model" in cfg.pricing
    assert cfg.pricing["custom-model"]["input_per_mtok"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/config/test_pricing_config.py -v`
Expected: FAIL with `AttributeError: ... object has no attribute 'pricing'` or similar

- [ ] **Step 3: Add pricing field to JanusConfig**

In `src/janus/config/schema.py`, add the `pricing` field to the `JanusConfig` class:

```python
class JanusConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    providers: list[ProviderConfig] = Field(default_factory=list)
    combos: list[ComboConfig] = Field(default_factory=list)
    api_keys: list[str] = Field(default_factory=list)
    token_savers: TokenSaverConfig = Field(default_factory=TokenSaverConfig)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/config/test_pricing_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/janus/config/schema.py tests/unit/config/test_pricing_config.py
git commit -m "feat: add pricing config field"
```

---

## Task 6: DB Schema Migration — New Columns + Budgets Table

**Files:**
- Modify: `src/janus/storage/database.py`
- Test: `tests/unit/storage/test_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/storage/test_migration.py`:

```python
import aiosqlite
import pytest

from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_usage_table_has_new_columns(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(usage)") as cur:
            rows = await cur.fetchall()
    columns = {row[1] for row in rows}
    assert "cost" in columns
    assert "cache_creation_tokens" in columns
    assert "cache_read_tokens" in columns
    assert "client_key_id" in columns


@pytest.mark.asyncio
async def test_budgets_table_exists(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(budgets)") as cur:
            rows = await cur.fetchall()
    columns = {row[1] for row in rows}
    assert "id" in columns
    assert "key_id" in columns
    assert "daily_limit" in columns
    assert "warn_pct" in columns
    assert "is_active" in columns
    assert "created_at" in columns


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(usage)") as cur:
            rows = await cur.fetchall()
    column_names = [row[1] for row in rows]
    assert column_names.count("cost") == 1
    assert column_names.count("cache_creation_tokens") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_migration.py -v`
Expected: FAIL (columns not found, budgets table not found)

- [ ] **Step 3: Update database.py with migration**

Replace the entire contents of `src/janus/storage/database.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    provider_id TEXT,
    model TEXT,
    account_id TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    status INTEGER
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER,
    daily_limit REAL NOT NULL,
    warn_pct REAL DEFAULT 80,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (key_id) REFERENCES api_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_cost_key ON usage(client_key_id, date(timestamp));
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider_id);
"""

_NEW_USAGE_COLUMNS = [
    ("cost", "REAL DEFAULT 0.0"),
    ("cache_creation_tokens", "INTEGER DEFAULT 0"),
    ("cache_read_tokens", "INTEGER DEFAULT 0"),
    ("client_key_id", "INTEGER"),
]


async def _migrate_usage_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(usage)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    for col, col_type in _NEW_USAGE_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE usage ADD COLUMN {col} {col_type}")


async def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA)
        await _migrate_usage_columns(db)
        await db.commit()


@asynccontextmanager
async def get_connection(db_path: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        yield db
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_migration.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run existing storage tests to ensure no regression**

Run: `.venv/bin/python -m pytest tests/unit/storage/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/janus/storage/database.py tests/unit/storage/test_migration.py
git commit -m "feat: add DB migration for cost, cache tokens, client_key_id, budgets table"
```

---

## Task 7: Update record_usage() with New Parameters

**Files:**
- Modify: `src/janus/storage/usage.py`
- Modify: `tests/unit/storage/test_usage.py`

- [ ] **Step 1: Update existing tests and add new ones**

Replace `tests/unit/storage/test_usage.py`:

```python
import pytest

from janus.storage.database import init_db
from janus.storage.usage import get_usage_stats, record_usage


@pytest.mark.asyncio
async def test_record_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50


@pytest.mark.asyncio
async def test_record_multiple_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="an",
        model="claude",
        input_tokens=200,
        output_tokens=100,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=50,
        output_tokens=25,
        status=429,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 3
    assert stats["total_input_tokens"] == 350
    assert stats["total_output_tokens"] == 175


@pytest.mark.asyncio
async def test_usage_stats_by_model(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=200,
        output_tokens=100,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="an",
        model="claude",
        input_tokens=50,
        output_tokens=25,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    by_model = {m["model"]: m for m in stats["by_model"]}
    assert by_model["glm-4.7"]["requests"] == 2
    assert by_model["glm-4.7"]["input_tokens"] == 300
    assert by_model["claude"]["requests"] == 1


@pytest.mark.asyncio
async def test_record_usage_with_cost_and_cache(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="an",
        model="claude-sonnet-4-20250514",
        account_id="an-0",
        input_tokens=1000,
        output_tokens=500,
        cache_creation_tokens=200,
        cache_read_tokens=800,
        status=200,
        client_key_id=1,
        cost=0.015,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1


@pytest.mark.asyncio
async def test_record_usage_defaults_backward_compatible(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_usage.py -v`
Expected: FAIL (cost/cache params not accepted)

- [ ] **Step 3: Update record_usage()**

Replace `src/janus/storage/usage.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)


async def record_usage(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    account_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    status: int = 0,
    client_key_id: int | None = None,
    cost: float = 0.0,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (provider_id, model, account_id, input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, status, client_key_id, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider_id,
                    model,
                    account_id,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    status,
                    client_key_id,
                    cost,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)


async def get_usage_stats(db_path: str | Path) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(input_tokens),0) as inp,"
            "COALESCE(SUM(output_tokens),0) as outp FROM usage"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
        total_requests = row["cnt"]
        total_input = row["inp"]
        total_output = row["outp"]

        async with db.execute(
            """SELECT model, COUNT(*) as requests,
                      COALESCE(SUM(input_tokens),0) as input_tokens,
                      COALESCE(SUM(output_tokens),0) as output_tokens
               FROM usage GROUP BY model ORDER BY requests DESC"""
        ) as cur:
            model_rows = await cur.fetchall()

    return {
        "total_requests": total_requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "by_model": [dict(r) for r in model_rows],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_usage.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/usage.py tests/unit/storage/test_usage.py
git commit -m "feat: update record_usage with cost, cache tokens, client_key_id"
```

---

## Task 8: Update verify_key() to Return key_id

**Files:**
- Modify: `src/janus/storage/api_keys.py:30-40`
- Test: `tests/unit/storage/test_api_keys.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/storage/test_api_keys.py` (create if it does not exist, or append):

```python
import pytest

from janus.storage.api_keys import create_key, verify_key
from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_verify_key_returns_id(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    raw_key, record = await create_key(db_path, name="test")
    result = await verify_key(db_path, raw_key)
    assert result == record["id"]


@pytest.mark.asyncio
async def test_verify_key_returns_none_for_invalid(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    result = await verify_key(db_path, "sk-janus-deadbeef")
    assert result is None


@pytest.mark.asyncio
async def test_verify_revoked_key_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    raw_key, record = await create_key(db_path, name="test")
    from janus.storage.api_keys import revoke_key
    await revoke_key(db_path, record["id"])
    result = await verify_key(db_path, raw_key)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_api_keys.py -v`
Expected: FAIL (verify_key returns bool, not int)

- [ ] **Step 3: Update verify_key()**

In `src/janus/storage/api_keys.py`, replace the `verify_key` function:

```python
async def verify_key(db_path: str | Path, key: str) -> int | None:
    if not key.startswith("sk-janus-"):
        return None
    key_hash = _hash_key(key)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return row["id"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_api_keys.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/api_keys.py tests/unit/storage/test_api_keys.py
git commit -m "feat: verify_key returns key_id instead of bool"
```

---

## Task 9: Update Auth Dependency to Expose key_id

**Files:**
- Modify: `src/janus/api/deps.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_key_id_passthrough.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry
from janus.storage.api_keys import create_key
from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_request_with_db_key_stores_key_id(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    config = JanusConfig(
        server=ServerSettings(require_api_key=True, data_dir=tmp_path),
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    raw_key, record = await create_key(db_path, name="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    import aiosqlite
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT client_key_id FROM usage") as cur:
            rows = await cur.fetchall()
    if rows:
        assert rows[0]["client_key_id"] == record["id"]
```

- [ ] **Step 2: Run test to verify it fails or has no key_id stored**

Run: `.venv/bin/python -m pytest tests/integration/test_key_id_passthrough.py -v`
Expected: FAIL or client_key_id is NULL (auth dependency doesn't pass key_id)

- [ ] **Step 3: Update deps.py to expose key_id**

Replace `src/janus/api/deps.py`:

```python
from __future__ import annotations

from fastapi import Header, HTTPException, Request


async def require_api_key(request: Request, authorization: str = Header(default="")) -> None:
    config = request.app.state.config
    if not config.server.require_api_key:
        return
    if authorization.startswith("Bearer "):
        key = authorization[7:]
        if key in config.api_keys:
            return
        from janus.storage.api_keys import verify_key

        db_path = request.app.state.db_path
        key_id = await verify_key(db_path, key)
        if key_id is not None:
            request.state.client_key_id = key_id
            return
    raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 4: Commit (will wire this into routes in Task 12)**

```bash
git add src/janus/api/deps.py tests/integration/test_key_id_passthrough.py
git commit -m "feat: auth dependency exposes resolved key_id on request state"
```

---

## Task 10: Analytics Query Layer

**Files:**
- Create: `src/janus/storage/analytics.py`
- Create: `tests/unit/storage/test_analytics.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/usage_seed.py`

- [ ] **Step 1: Create fixtures package**

Create `tests/fixtures/__init__.py` as an empty file.

- [ ] **Step 2: Create usage seed helper**

Create `tests/fixtures/usage_seed.py`:

```python
from __future__ import annotations

import datetime
from pathlib import Path

from janus.storage.database import get_connection


async def seed_usage(
    db_path: str | Path,
    rows: list[dict],
) -> None:
    for row in rows:
        ts = row.get("timestamp", datetime.datetime.now().isoformat())
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (timestamp, provider_id, model, account_id,
                    input_tokens, output_tokens, cache_creation_tokens,
                    cache_read_tokens, status, client_key_id, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    row.get("provider_id"),
                    row.get("model"),
                    row.get("account_id"),
                    row.get("input_tokens", 0),
                    row.get("output_tokens", 0),
                    row.get("cache_creation_tokens", 0),
                    row.get("cache_read_tokens", 0),
                    row.get("status", 200),
                    row.get("client_key_id"),
                    row.get("cost", 0.0),
                ),
            )
            await db.commit()
```

- [ ] **Step 3: Write the failing tests**

Create `tests/unit/storage/test_analytics.py`:

```python
import datetime

import pytest

from janus.storage.analytics import (
    get_breakdown,
    get_spend_summary,
    get_success_rate,
)
from janus.storage.database import init_db
from tests.fixtures.usage_seed import seed_usage


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


@pytest.mark.asyncio
async def test_get_spend_summary_empty(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    result = await get_spend_summary(db_path, days=30)
    assert result["total_cost"] == 0.0
    assert result["total_requests"] == 0
    assert result["daily"] == []


@pytest.mark.asyncio
async def test_get_spend_summary_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"timestamp": _ts(0), "model": "gpt-4o", "input_tokens": 1000, "output_tokens": 500, "cost": 0.01, "status": 200},
            {"timestamp": _ts(1), "model": "gpt-4o", "input_tokens": 2000, "output_tokens": 1000, "cost": 0.02, "status": 200},
            {"timestamp": _ts(0), "model": "claude-sonnet-4-20250514", "input_tokens": 500, "output_tokens": 250, "cost": 0.005, "status": 500},
        ],
    )
    result = await get_spend_summary(db_path, days=30)
    assert result["total_requests"] == 3
    assert abs(result["total_cost"] - 0.035) < 0.0001
    assert result["total_input_tokens"] == 3500
    assert result["total_output_tokens"] == 1750
    assert len(result["daily"]) >= 1


@pytest.mark.asyncio
async def test_get_breakdown_by_model(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"timestamp": _ts(0), "model": "gpt-4o", "input_tokens": 1000, "output_tokens": 500, "cost": 0.01, "status": 200},
            {"timestamp": _ts(0), "model": "gpt-4o", "input_tokens": 500, "output_tokens": 250, "cost": 0.005, "status": 200},
            {"timestamp": _ts(0), "model": "claude", "input_tokens": 300, "output_tokens": 100, "cost": 0.003, "status": 200},
        ],
    )
    result = await get_breakdown(db_path, dimension="model", days=30)
    assert len(result) == 2
    gpt = [r for r in result if r["model"] == "gpt-4o"][0]
    assert gpt["requests"] == 2
    assert abs(gpt["cost"] - 0.015) < 0.0001


@pytest.mark.asyncio
async def test_get_breakdown_by_provider(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"timestamp": _ts(0), "provider_id": "openai", "model": "gpt-4o", "cost": 0.01, "status": 200},
            {"timestamp": _ts(0), "provider_id": "anthropic", "model": "claude", "cost": 0.02, "status": 200},
        ],
    )
    result = await get_breakdown(db_path, dimension="provider", days=30)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_success_rate(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 200},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 200},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 500},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 429},
        ],
    )
    result = await get_success_rate(db_path, days=30)
    assert result["success_2xx"] == 2
    assert result["client_4xx"] == 1
    assert result["server_5xx"] == 1
    assert result["total"] == 4
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_analytics.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 5: Write the analytics implementation**

Create `src/janus/storage/analytics.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .database import get_connection

Dimension = Literal["model", "provider", "account", "client_key"]

_DIMENSION_COLUMN = {
    "model": "model",
    "provider": "provider_id",
    "account": "account_id",
    "client_key": "client_key_id",
}


async def get_spend_summary(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT COUNT(*) as cnt,
                      COALESCE(SUM(input_tokens), 0) as inp,
                      COALESCE(SUM(output_tokens), 0) as outp,
                      COALESCE(SUM(cache_creation_tokens), 0) as cc,
                      COALESCE(SUM(cache_read_tokens), 0) as cr,
                      COALESCE(SUM(cost), 0.0) as cost
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None

        async with db.execute(
            """SELECT date(timestamp) as date,
                      COUNT(*) as requests,
                      COALESCE(SUM(cost), 0.0) as cost
               FROM usage
               WHERE timestamp >= datetime('now', ?)
               GROUP BY date(timestamp)
               ORDER BY date(timestamp)""",
            (f"-{days} days",),
        ) as cur:
            daily_rows = await cur.fetchall()

    return {
        "total_cost": row["cost"],
        "total_requests": row["cnt"],
        "total_input_tokens": row["inp"],
        "total_output_tokens": row["outp"],
        "total_cache_creation_tokens": row["cc"],
        "total_cache_read_tokens": row["cr"],
        "daily": [dict(r) for r in daily_rows],
    }


async def get_breakdown(
    db_path: str | Path, *, dimension: Dimension, days: int = 30
) -> list[dict[str, Any]]:
    col = _DIMENSION_COLUMN[dimension]
    async with get_connection(db_path) as db:
        async with db.execute(
            f"""SELECT {col} as {dimension},
                       COUNT(*) as requests,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(cost), 0.0) as cost
                FROM usage
                WHERE timestamp >= datetime('now', ?)
                GROUP BY {col}
                ORDER BY cost DESC""",
            (f"-{days} days",),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_success_rate(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                SUM(CASE WHEN status >= 200 AND status < 300 THEN 1 ELSE 0 END) as s2xx,
                SUM(CASE WHEN status >= 400 AND status < 500 THEN 1 ELSE 0 END) as s4xx,
                SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) as s5xx,
                COUNT(*) as total
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
    return {
        "success_2xx": row["s2xx"] or 0,
        "client_4xx": row["s4xx"] or 0,
        "server_5xx": row["s5xx"] or 0,
        "total": row["total"] or 0,
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_analytics.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add src/janus/storage/analytics.py tests/unit/storage/test_analytics.py tests/fixtures/
git commit -m "feat: add analytics query layer (spend summary, breakdown, success rate)"
```

---

## Task 11: Budget CRUD + Status

**Files:**
- Create: `src/janus/storage/budgets.py`
- Create: `tests/unit/storage/test_budgets.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/storage/test_budgets.py`:

```python
import datetime

import pytest

from janus.storage.budgets import (
    create_or_update_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
)
from janus.storage.database import init_db
from tests.fixtures.usage_seed import seed_usage


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


@pytest.mark.asyncio
async def test_create_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    budget_id = await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    budgets = await get_budgets(db_path)
    assert len(budgets) == 1
    assert budgets[0]["daily_limit"] == 5.0
    assert budgets[0]["id"] == budget_id


@pytest.mark.asyncio
async def test_update_budget_replaces(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=90)
    budgets = await get_budgets(db_path)
    assert len(budgets) == 1
    assert budgets[0]["daily_limit"] == 10.0
    assert budgets[0]["warn_pct"] == 90


@pytest.mark.asyncio
async def test_budget_status_no_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    status = await get_budget_status(db_path, key_id=None)
    assert status is None


@pytest.mark.asyncio
async def test_budget_status_ok(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 3.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status is not None
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 3.0) < 0.0001
    assert abs(status["daily_limit"] - 10.0) < 0.0001


@pytest.mark.asyncio
async def test_budget_status_warning(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=50)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 6.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "warning"


@pytest.mark.asyncio
async def test_budget_status_exceeded(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 6.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "exceeded"


@pytest.mark.asyncio
async def test_per_key_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    from janus.storage.api_keys import create_key
    _, key_record = await create_key(db_path, name="test-key")
    key_id = key_record["id"]
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=2.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 1.0, "status": 200, "client_key_id": key_id}])
    status = await get_budget_status(db_path, key_id=key_id)
    assert status is not None
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 1.0) < 0.0001


@pytest.mark.asyncio
async def test_only_today_counts(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(2), "cost": 10.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 0.0) < 0.0001


@pytest.mark.asyncio
async def test_delete_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    budget_id = await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    deleted = await delete_budget(db_path, budget_id)
    assert deleted is True
    budgets = await get_budgets(db_path)
    assert len(budgets) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_budgets.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write the budgets implementation**

Create `src/janus/storage/budgets.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def create_or_update_budget(
    db_path: str | Path,
    *,
    key_id: int | None,
    daily_limit: float,
    warn_pct: float = 80,
) -> int:
    async with get_connection(db_path) as db:
        if key_id is not None:
            async with db.execute(
                "SELECT id FROM budgets WHERE key_id = ? AND is_active = 1",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT id FROM budgets WHERE key_id IS NULL AND is_active = 1"
            ) as cur:
                row = await cur.fetchone()
        if row is not None:
            await db.execute(
                "UPDATE budgets SET daily_limit = ?, warn_pct = ? WHERE id = ?",
                (daily_limit, warn_pct, row["id"]),
            )
            await db.commit()
            return row["id"]
        cursor = await db.execute(
            "INSERT INTO budgets (key_id, daily_limit, warn_pct) VALUES (?, ?, ?)",
            (key_id, daily_limit, warn_pct),
        )
        await db.commit()
        return cursor.lastrowid


async def get_budgets(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, key_id, daily_limit, warn_pct, is_active, created_at "
            "FROM budgets WHERE is_active = 1 ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_budget(db_path: str | Path, budget_id: int) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM budgets WHERE id = ?", (budget_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_budget_status(
    db_path: str | Path, *, key_id: int | None = None
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        if key_id is not None:
            async with db.execute(
                "SELECT daily_limit, warn_pct FROM budgets WHERE key_id = ? AND is_active = 1",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE client_key_id = ? AND date(timestamp) = date('now', 'localtime')",
                (key_id,),
            ) as cur:
                spent_row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT daily_limit, warn_pct FROM budgets WHERE key_id IS NULL AND is_active = 1"
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE date(timestamp) = date('now', 'localtime')"
            ) as cur:
                spent_row = await cur.fetchone()

    assert row is not None
    assert spent_row is not None
    daily_limit = row["daily_limit"]
    today_spend = spent_row["spent"]
    pct_used = (today_spend / daily_limit * 100) if daily_limit > 0 else 100.0
    if pct_used >= 100:
        status = "exceeded"
    elif pct_used >= row["warn_pct"]:
        status = "warning"
    else:
        status = "ok"
    return {
        "daily_limit": daily_limit,
        "today_spend": today_spend,
        "remaining": max(0.0, daily_limit - today_spend),
        "pct_used": pct_used,
        "status": status,
        "warn_pct": row["warn_pct"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_budgets.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/budgets.py tests/unit/storage/test_budgets.py
git commit -m "feat: add budget CRUD and status queries"
```

---

## Task 12: Wire PricingRegistry into App State

**Files:**
- Modify: `src/janus/app.py`

- [ ] **Step 1: Add PricingRegistry to app factory**

In `src/janus/app.py`, add import and wire it:

Add to imports at top:
```python
from janus.pricing.registry import PricingRegistry
```

After the `app.state.saver_pipeline = SaverPipeline(savers)` line (line 53), add:
```python
    app.state.pricing_registry = PricingRegistry(config.pricing)
```

- [ ] **Step 2: Verify app still imports**

Run: `.venv/bin/python -c "from janus.app import create_app; create_app()"`
Expected: No error

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All existing tests pass

- [ ] **Step 4: Commit**

```bash
git add src/janus/app.py
git commit -m "feat: wire PricingRegistry into app state"
```

---

## Task 13: Budget Enforcement + Cost Recording in _handle()

**Files:**
- Modify: `src/janus/api/routes.py`

- [ ] **Step 1: Write the failing integration test for budget enforcement**

Create `tests/integration/test_budget_enforcement.py`:

```python
import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget
from janus.storage.database import init_db, get_connection


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


async def _seed_cost(db_path, cost: float, key_id=None) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO usage (timestamp, model, cost, status, client_key_id) "
            "VALUES (?, 'test', ?, 200, ?)",
            (_ts(0), cost, key_id),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_request_blocked_when_budget_exceeded(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    config = JanusConfig(
        server=ServerSettings(require_api_key=True, data_dir=tmp_path),
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    raw_key, record = await create_key(db_path, name="test")
    await create_or_update_budget(db_path, key_id=record["id"], daily_limit=1.0, warn_pct=80)
    await _seed_cost(db_path, 1.5, record["id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert resp.status_code == 429
    body = resp.json()
    assert "budget" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_request_passes_when_no_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    config = JanusConfig(
        server=ServerSettings(require_api_key=True, data_dir=tmp_path),
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    raw_key, _ = await create_key(db_path, name="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert resp.status_code != 429


@pytest.mark.asyncio
async def test_budget_block_response_has_retry_after(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    config = JanusConfig(
        server=ServerSettings(require_api_key=True, data_dir=tmp_path),
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    raw_key, record = await create_key(db_path, name="test")
    await create_or_update_budget(db_path, key_id=record["id"], daily_limit=0.5, warn_pct=50)
    await _seed_cost(db_path, 1.0, record["id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers.keys()}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_budget_enforcement.py -v`
Expected: FAIL (no budget enforcement in _handle)

- [ ] **Step 3: Implement budget enforcement + cost recording in _handle()**

Replace the `_handle` function in `src/janus/api/routes.py`:

```python
async def _handle(
    client_format: str,
    body: dict[str, Any],
    request: Request,
) -> Response:
    handler: FallbackHandler = request.app.state.fallback_handler
    db_path = request.app.state.db_path
    pricing_registry = request.app.state.pricing_registry

    client_key_id = getattr(request.state, "client_key_id", None)

    blocked_response = await _check_budgets(db_path, client_key_id)
    if blocked_response is not None:
        return blocked_response

    client_adapter = FORMATS[client_format]
    canonical_req = client_adapter.parse_request(body)

    saver_pipeline: SaverPipeline = request.app.state.saver_pipeline
    canonical_req = saver_pipeline.apply(canonical_req)

    try:
        attempts = handler.resolve_attempts(canonical_req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    last_error = "Unknown error"
    for target in attempts:
        provider_adapter = _resolve_format(target.native_format)
        upstream_payload = provider_adapter.build_upstream_request(canonical_req, target.model)
        provider = _build_provider(target.provider_config)

        try:
            if canonical_req.stream:
                result = await provider.call(upstream_payload, stream=True)
                if result.status_code >= 400:
                    if is_fallback_eligible(result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(result.status_code).value,
                        )
                        last_error = f"{target.account_id}: {result.status_code}"
                        continue
                    raise HTTPException(
                        status_code=result.status_code,
                        detail=(str(result.json_data) if result.json_data else "Upstream error"),
                    )
                lines = result.lines
                if lines is None:
                    raise HTTPException(status_code=502, detail="No stream from upstream")
                parser = provider_adapter.stream_parser()
                emitter = client_adapter.stream_emitter()
                generator = translate_stream(lines, parser, emitter)
                return StreamingResponse(generator, media_type="text/event-stream")

            result = await provider.call(upstream_payload, stream=False)
            if result.status_code >= 400:
                if is_fallback_eligible(result.status_code):
                    handler.mark_cooldown(
                        target.account_id,
                        classify_error(result.status_code).value,
                    )
                    last_error = f"{target.account_id}: {result.status_code}"
                    continue
                raise HTTPException(
                    status_code=result.status_code,
                    detail=(str(result.json_data) if result.json_data else "Upstream error"),
                )
            if result.json_data is None:
                raise HTTPException(status_code=502, detail="Empty upstream response")
            canonical_resp = provider_adapter.parse_upstream_response(result.json_data)
            client_payload = client_adapter.emit_response(canonical_resp)

            from janus.pricing.calculator import compute_cost
            from janus.storage.usage import record_usage

            cost = compute_cost(canonical_resp.usage, target.model, pricing_registry)
            await record_usage(
                db_path,
                provider_id=target.provider_config.id,
                model=target.model,
                account_id=target.account_id,
                input_tokens=canonical_resp.usage.input_tokens,
                output_tokens=canonical_resp.usage.output_tokens,
                cache_creation_tokens=canonical_resp.usage.cache_creation_input_tokens,
                cache_read_tokens=canonical_resp.usage.cache_read_input_tokens,
                status=result.status_code,
                client_key_id=client_key_id,
                cost=cost,
            )

            return JSONResponse(content=client_payload)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            handler.mark_cooldown(target.account_id, "network")
            last_error = f"{target.account_id}: {type(e).__name__}"
            continue

    raise HTTPException(status_code=503, detail=f"All providers exhausted: {last_error}")
```

Add this helper function above `_handle`:

```python
import datetime

from janus.storage.budgets import get_budget_status


async def _check_budgets(
    db_path: str | Path,
    client_key_id: int | None,
) -> Response | None:
    try:
        statuses: list[dict[str, Any]] = []
        key_status = await get_budget_status(db_path, key_id=client_key_id)
        if key_status is not None:
            statuses.append(key_status)
        global_status = await get_budget_status(db_path, key_id=None)
        if global_status is not None:
            statuses.append(global_status)
        for s in statuses:
            if s["status"] == "exceeded":
                now = datetime.datetime.now()
                midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
                retry_after = int((midnight - now).total_seconds()) + 1
                error_body: dict[str, Any] = {
                    "error": {
                        "message": (
                            f"Daily budget exceeded. "
                            f"Spent ${s['today_spend']:.2f} of ${s['daily_limit']:.2f} limit. "
                            f"Resets at midnight."
                        ),
                        "type": "budget_exceeded",
                        "today_spend": round(s["today_spend"], 4),
                        "daily_limit": s["daily_limit"],
                    }
                }
                return JSONResponse(
                    content=error_body,
                    status_code=429,
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
    except Exception:
        pass
    return None
```

Add `from pathlib import Path` and `import datetime` to the imports at the top of `routes.py`.

- [ ] **Step 4: Run budget enforcement tests**

Run: `.venv/bin/python -m pytest tests/integration/test_budget_enforcement.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Lint and typecheck**

Run: `.venv/bin/ruff check src/janus/ tests/` and `.venv/bin/mypy src/janus/`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_budget_enforcement.py
git commit -m "feat: budget enforcement + cost recording in request handler"
```

---

## Task 14: Cost Recording Integration Test

**Files:**
- Create: `tests/integration/test_cost_recording.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_cost_recording.py`:

```python
import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

import respx
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_cost_recorded_for_non_streaming(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    provider = ProviderConfig(
        id="test-openai",
        prefix="test",
        api_type="openai_compat",
        base_url="https://api.test.com/v1",
        api_key="sk-test",
        models=["gpt-4o"],
    )
    registry.register(provider)
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    mock_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }

    with respx.mock(base_url="https://api.test.com/v1") as mock:
        mock.post("/chat/completions").respond(200, json=mock_response)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "test/gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cost, input_tokens, output_tokens FROM usage WHERE model = 'gpt-4o'") as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["cost"] > 0
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 50
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/integration/test_cost_recording.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_cost_recording.py
git commit -m "test: cost recording integration test"
```

---

## Task 15: Dashboard — Chart.js + Nav Updates

**Files:**
- Modify: `src/janus/dashboard/templates/base.html`

- [ ] **Step 1: Add Chart.js CDN and nav items**

In `src/janus/dashboard/templates/base.html`, add Chart.js CDN after the HTMX script tag (line 8):

```html
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
```

Add "Analytics" and "Budgets" nav items between the Usage and bottom section (after line 43, after the Usage `</a>`):

```html
                <a href="/dashboard/analytics" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block analytics_active %}{% endblock %}">
                    <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z"/></svg>
                    Analytics
                </a>
                <a href="/dashboard/budgets" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block budgets_active %}{% endblock %}">
                    <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1"/></svg>
                    Budgets
                </a>
```

- [ ] **Step 2: Commit**

```bash
git add src/janus/dashboard/templates/base.html
git commit -m "feat: add Chart.js CDN and analytics/budgets nav items"
```

---

## Task 16: Dashboard — Analytics Page

**Files:**
- Create: `src/janus/dashboard/templates/analytics.html`
- Modify: `src/janus/dashboard/routes.py`
- Test: `tests/integration/test_dashboard_analytics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dashboard_analytics.py`:

```python
import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.providers.registry import ProviderRegistry
from janus.storage.database import init_db, get_connection


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


async def _seed(db_path, rows):
    for row in rows:
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO usage (timestamp, model, provider_id, input_tokens, output_tokens, cost, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.get("ts", _ts(0)),
                    row.get("model", "gpt-4o"),
                    row.get("provider_id", "openai"),
                    row.get("input_tokens", 1000),
                    row.get("output_tokens", 500),
                    row.get("cost", 0.01),
                    row.get("status", 200),
                ),
            )
            await db.commit()


@pytest.mark.asyncio
async def test_analytics_page_renders(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await _seed(db_path, [{"ts": _ts(0), "cost": 0.05, "model": "gpt-4o"}, {"ts": _ts(1), "cost": 0.03, "model": "claude"}])

    registry = ProviderRegistry()
    config = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/dashboard/analytics")
    assert resp.status_code == 200
    assert "Analytics" in resp.text
    assert "0.08" in resp.text or "0.0" in resp.text


@pytest.mark.asyncio
async def test_analytics_breakdown_by_provider(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await _seed(db_path, [
        {"ts": _ts(0), "provider_id": "openai", "cost": 0.05},
        {"ts": _ts(0), "provider_id": "anthropic", "cost": 0.03},
    ])

    registry = ProviderRegistry()
    config = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/dashboard/analytics?dimension=provider&days=30")
    assert resp.status_code == 200
    assert "openai" in resp.text
    assert "anthropic" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_dashboard_analytics.py -v`
Expected: FAIL (no analytics route)

- [ ] **Step 3: Add analytics route to dashboard**

In `src/janus/dashboard/routes.py`, add imports at the top (after existing imports):

```python
from janus.storage.analytics import get_breakdown, get_spend_summary, get_success_rate
```

Add the analytics route after the usage route:

```python
@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    days: int = 30,
    dimension: str = "model",
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    try:
        summary = await get_spend_summary(db_path, days=days)
        breakdown = await get_breakdown(db_path, dimension=dimension, days=days)
        success = await get_success_rate(db_path, days=days)
    except Exception:
        summary = {"total_cost": 0, "total_requests": 0, "daily": []}
        breakdown = []
        success = {"success_2xx": 0, "client_4xx": 0, "server_5xx": 0, "total": 0}
    context: dict[str, Any] = {
        "request": request,
        "summary": summary,
        "breakdown": breakdown,
        "success": success,
        "days": days,
        "dimension": dimension,
    }
    return _templates.TemplateResponse(request, "analytics.html", context)
```

- [ ] **Step 4: Create the analytics template**

Create `src/janus/dashboard/templates/analytics.html`:

```html
{% extends "base.html" %}
{% block title %}Analytics - Janus{% endblock %}
{% block analytics_active %}bg-gray-700 text-white{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Analytics</h1>

<!-- Date Range Selector -->
<div class="flex space-x-2 mb-6">
    {% for d in [7, 30, 90] %}
    <a href="/dashboard/analytics?days={{ d }}&dimension={{ dimension }}"
       class="px-4 py-2 rounded-lg text-sm font-medium {% if days == d %}bg-blue-600 text-white{% else %}bg-gray-800 text-gray-400 hover:text-white{% endif %} transition-colors">
        {{ d }}d
    </a>
    {% endfor %}
</div>

<!-- Stats Cards -->
<div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-8">
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Total Cost</p>
        <p class="text-3xl font-bold text-white">${{ "%.4f"|format(summary.total_cost) }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Total Requests</p>
        <p class="text-3xl font-bold text-white">{{ summary.total_requests }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Input Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(summary.total_input_tokens) }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Output Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(summary.total_output_tokens) }}</p>
    </div>
</div>

<!-- Spend Chart -->
<div class="bg-gray-800 rounded-xl p-6 border border-gray-700 mb-8">
    <h2 class="text-lg font-semibold text-white mb-4">Spend Over Time</h2>
    <canvas id="spendChart" height="80"></canvas>
</div>

<!-- Breakdown -->
<div class="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden mb-8">
    <div class="px-6 py-4 border-b border-gray-700 flex items-center justify-between">
        <h2 class="text-lg font-semibold text-white">Breakdown</h2>
        <div class="flex space-x-1">
            {% for dim, label in [("model", "Model"), ("provider", "Provider"), ("account", "Account"), ("client_key", "Key")] %}
            <a href="/dashboard/analytics?days={{ days }}&dimension={{ dim }}"
               class="px-3 py-1 rounded-md text-xs font-medium {% if dimension == dim %}bg-blue-600 text-white{% else %}bg-gray-700 text-gray-400 hover:text-white{% endif %} transition-colors">
                {{ label }}
            </a>
            {% endfor %}
        </div>
    </div>
    {% if breakdown %}
    <table class="w-full">
        <thead>
            <tr class="bg-gray-900 border-b border-gray-700">
                <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">{{ dimension }}</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Requests</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Input</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Output</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Cost</th>
            </tr>
        </thead>
        <tbody class="divide-y divide-gray-700">
            {% for row in breakdown %}
            <tr class="hover:bg-gray-750">
                <td class="px-4 py-3 text-sm text-white font-mono">{{ row[dimension] or '—' }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ row.requests }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ "{:,}".format(row.input_tokens) }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ "{:,}".format(row.output_tokens) }}</td>
                <td class="px-4 py-3 text-sm text-green-400 text-right">${{ "%.4f"|format(row.cost) }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="px-6 py-12 text-center"><p class="text-gray-400">No data for this range.</p></div>
    {% endif %}
</div>

<!-- Success Rate -->
<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
    <div class="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h2 class="text-lg font-semibold text-white mb-4">Success Rate</h2>
        <canvas id="successChart" height="120"></canvas>
    </div>
    <div class="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h2 class="text-lg font-semibold text-white mb-4">Status Breakdown</h2>
        <div class="space-y-3">
            <div class="flex items-center justify-between">
                <span class="text-green-400 text-sm">2xx Success</span>
                <span class="text-white font-bold">{{ success.success_2xx }}</span>
            </div>
            <div class="flex items-center justify-between">
                <span class="text-yellow-400 text-sm">4xx Client Error</span>
                <span class="text-white font-bold">{{ success.client_4xx }}</span>
            </div>
            <div class="flex items-center justify-between">
                <span class="text-red-400 text-sm">5xx Server Error</span>
                <span class="text-white font-bold">{{ success.server_5xx }}</span>
            </div>
        </div>
    </div>
</div>

<script>
// Spend chart
const dailyData = {{ summary.daily | tojson }};
const ctxSpend = document.getElementById('spendChart');
new Chart(ctxSpend, {
    type: 'line',
    data: {
        labels: dailyData.map(d => d.date),
        datasets: [{
            label: 'Daily Cost ($)',
            data: dailyData.map(d => d.cost),
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.1)',
            fill: true,
            tension: 0.3,
        }]
    },
    options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#9ca3af' } } },
        scales: {
            x: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } },
            y: { ticks: { color: '#9ca3af' }, grid: { color: '#374151' } }
        }
    }
});

// Success donut
const successData = {{ success | tojson }};
const ctxSuccess = document.getElementById('successChart');
new Chart(ctxSuccess, {
    type: 'doughnut',
    data: {
        labels: ['2xx Success', '4xx Client Error', '5xx Server Error'],
        datasets: [{
            data: [successData.success_2xx, successData.client_4xx, successData.server_5xx],
            backgroundColor: ['#10b981', '#f59e0b', '#ef4444'],
        }]
    },
    options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#9ca3af' } } }
    }
});
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/integration/test_dashboard_analytics.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/janus/dashboard/templates/analytics.html src/janus/dashboard/routes.py tests/integration/test_dashboard_analytics.py
git commit -m "feat: dashboard analytics page with charts and breakdowns"
```

---

## Task 17: Dashboard — Budgets Page

**Files:**
- Create: `src/janus/dashboard/templates/budgets.html`
- Create: `src/janus/dashboard/templates/budgets_partial.html`
- Modify: `src/janus/dashboard/routes.py`

- [ ] **Step 1: Add budgets routes to dashboard**

In `src/janus/dashboard/routes.py`, add imports at the top:

```python
from janus.storage.budgets import (
    create_or_update_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
)
from janus.storage.api_keys import list_keys
```

Add the routes after the analytics route:

```python
@router.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    try:
        budgets = await get_budgets(db_path)
        keys = await list_keys(db_path)
        budget_statuses: list[dict[str, Any]] = []
        for b in budgets:
            status = await get_budget_status(db_path, key_id=b["key_id"])
            key_name = "Global"
            if b["key_id"] is not None:
                key_name = next((k["name"] for k in keys if k["id"] == b["key_id"]), f"Key #{b['key_id']}")
            budget_statuses.append({**b, "status": status, "key_name": key_name})
    except Exception:
        budget_statuses = []
        keys = []
    context: dict[str, Any] = {
        "request": request,
        "budgets": budget_statuses,
        "keys": keys,
    }
    return _templates.TemplateResponse(request, "budgets.html", context)


@router.post("/api/budgets", response_class=HTMLResponse)
async def create_budget(
    request: Request,
    key_select: str = Form(...),
    daily_limit: float = Form(...),
    warn_pct: float = Form(80),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    key_id: int | None = None
    if key_select != "global":
        key_id = int(key_select)
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=daily_limit, warn_pct=warn_pct)
    return await _budgets_partial(request, db_path)


@router.delete("/api/budgets/{budget_id}", response_class=HTMLResponse)
async def delete_budget_endpoint(request: Request, budget_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await delete_budget(db_path, budget_id)
    return await _budgets_partial(request, db_path)


async def _budgets_partial(request: Request, db_path: Path) -> HTMLResponse:
    budgets = await get_budgets(db_path)
    keys = await list_keys(db_path)
    budget_statuses: list[dict[str, Any]] = []
    for b in budgets:
        status = await get_budget_status(db_path, key_id=b["key_id"])
        key_name = "Global"
        if b["key_id"] is not None:
            key_name = next((k["name"] for k in keys if k["id"] == b["key_id"]), f"Key #{b['key_id']}")
        budget_statuses.append({**b, "status": status, "key_name": key_name})
    context: dict[str, Any] = {
        "request": request,
        "budgets": budget_statuses,
        "keys": keys,
    }
    return _templates.TemplateResponse(request, "budgets_partial.html", context)
```

- [ ] **Step 2: Create the budgets template**

Create `src/janus/dashboard/templates/budgets.html`:

```html
{% extends "base.html" %}
{% block title %}Budgets - Janus{% endblock %}
{% block budgets_active %}bg-gray-700 text-white{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Budgets</h1>

<!-- Create Budget Form -->
<div class="bg-gray-800 rounded-xl p-6 border border-gray-700 mb-8">
    <h2 class="text-lg font-semibold text-white mb-4">Create / Update Budget</h2>
    <form hx-post="/dashboard/api/budgets" hx-target="#budgets-table" class="flex flex-wrap items-end gap-4">
        <div>
            <label class="block text-gray-400 text-sm mb-1">Scope</label>
            <select name="key_select" class="bg-gray-900 text-white rounded-lg px-3 py-2 border border-gray-700 text-sm">
                <option value="global">Global (all keys)</option>
                {% for key in keys %}
                <option value="{{ key.id }}">{{ key.name }}</option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label class="block text-gray-400 text-sm mb-1">Daily Limit ($)</label>
            <input type="number" name="daily_limit" step="0.01" min="0" value="5.00"
                   class="bg-gray-900 text-white rounded-lg px-3 py-2 border border-gray-700 text-sm w-32">
        </div>
        <div>
            <label class="block text-gray-400 text-sm mb-1">Warn at (%)</label>
            <input type="number" name="warn_pct" step="1" min="1" max="100" value="80"
                   class="bg-gray-900 text-white rounded-lg px-3 py-2 border border-gray-700 text-sm w-24">
        </div>
        <button type="submit" class="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2 text-sm font-medium transition-colors">
            Set Budget
        </button>
    </form>
</div>

<!-- Budgets Table -->
<div id="budgets-table">
    {% include "budgets_partial.html" %}
</div>
{% endblock %}
```

- [ ] **Step 3: Create the budgets partial template**

Create `src/janus/dashboard/templates/budgets_partial.html`:

```html
<div class="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
    {% if budgets %}
    <table class="w-full">
        <thead>
            <tr class="bg-gray-900 border-b border-gray-700">
                <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Scope</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Daily Limit</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Today's Spend</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Usage</th>
                <th class="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase tracking-wider">Status</th>
                <th class="px-4 py-3"></th>
            </tr>
        </thead>
        <tbody class="divide-y divide-gray-700">
            {% for b in budgets %}
            <tr class="hover:bg-gray-750">
                <td class="px-4 py-3 text-sm text-white font-medium">{{ b.key_name }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">${{ "%.2f"|format(b.daily_limit) }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">
                    {% if b.status %}${{ "%.4f"|format(b.status.today_spend) }}{% else %}—{% endif %}
                </td>
                <td class="px-4 py-3 text-right">
                    {% if b.status %}
                    <div class="flex items-center justify-end">
                        <div class="w-24 bg-gray-700 rounded-full h-2 mr-2">
                            <div class="h-2 rounded-full {% if b.status.status == 'exceeded' %}bg-red-500{% elif b.status.status == 'warning' %}bg-yellow-500{% else %}bg-green-500{% endif %}"
                                 style="width: {{ [b.status.pct_used, 100] | min }}%"></div>
                        </div>
                        <span class="text-sm text-gray-400">{{ "%.0f"|format(b.status.pct_used) }}%</span>
                    </div>
                    {% else %}
                    <span class="text-sm text-gray-500">—</span>
                    {% endif %}
                </td>
                <td class="px-4 py-3 text-center">
                    {% if b.status %}
                    {% if b.status.status == 'exceeded' %}
                    <span class="inline-flex px-2 py-1 rounded-full text-xs font-medium bg-red-900 text-red-200">Exceeded</span>
                    {% elif b.status.status == 'warning' %}
                    <span class="inline-flex px-2 py-1 rounded-full text-xs font-medium bg-yellow-900 text-yellow-200">Warning</span>
                    {% else %}
                    <span class="inline-flex px-2 py-1 rounded-full text-xs font-medium bg-green-900 text-green-200">OK</span>
                    {% endif %}
                    {% else %}
                    <span class="text-sm text-gray-500">—</span>
                    {% endif %}
                </td>
                <td class="px-4 py-3 text-right">
                    <button hx-delete="/dashboard/api/budgets/{{ b.id }}" hx-target="#budgets-table"
                            class="text-red-400 hover:text-red-300 text-sm">Delete</button>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="px-6 py-12 text-center">
        <p class="text-gray-400">No budgets configured.</p>
    </div>
    {% endif %}
</div>
```

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/templates/budgets.html src/janus/dashboard/templates/budgets_partial.html src/janus/dashboard/routes.py
git commit -m "feat: dashboard budgets page with HTMX create/delete"
```

---

## Task 18: Dashboard — Enhanced Overview + Usage Pages

**Files:**
- Modify: `src/janus/dashboard/routes.py`
- Modify: `src/janus/dashboard/templates/overview.html`
- Modify: `src/janus/dashboard/templates/usage.html`

- [ ] **Step 1: Enhance overview route to include spend + budget data**

In `src/janus/dashboard/routes.py`, update the `overview` function. Replace the existing function with:

```python
@router.get("", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    stats = await _get_usage_stats_safe(db_path)
    registry = request.app.state.registry
    provider_count = len(registry.providers)
    today_cost = 0.0
    global_budget = None
    try:
        summary = await get_spend_summary(db_path, days=1)
        today_cost = summary["total_cost"]
        global_budget = await get_budget_status(db_path, key_id=None)
    except Exception:
        pass
    context: dict[str, Any] = {
        "request": request,
        "stats": stats,
        "provider_count": provider_count,
        "combos": registry.combos,
        "today_cost": today_cost,
        "global_budget": global_budget,
    }
    return _templates.TemplateResponse(request, "overview.html", context)
```

- [ ] **Step 2: Update overview template to show today's spend + budget bar**

Replace `src/janus/dashboard/templates/overview.html`:

```html
{% extends "base.html" %}
{% block title %}Overview - Janus{% endblock %}
{% block overview_active %}bg-gray-700 text-white{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Overview</h1>

<!-- Stats Cards -->
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Total Requests</p>
        <p class="text-3xl font-bold text-white">{{ stats.total_requests }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Input Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(stats.total_input_tokens) }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Output Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(stats.total_output_tokens) }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Active Providers</p>
        <p class="text-3xl font-bold text-white">{{ provider_count }}</p>
    </div>
</div>

<!-- Today's Spend + Budget -->
<div class="bg-gray-800 rounded-xl p-6 border border-gray-700 mb-8">
    <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold text-white">Today's Spend</h2>
        <span class="text-2xl font-bold text-green-400">${{ "%.4f"|format(today_cost) }}</span>
    </div>
    {% if global_budget %}
    <div class="flex items-center gap-3">
        <div class="flex-1 bg-gray-700 rounded-full h-3">
            <div class="h-3 rounded-full {% if global_budget.status == 'exceeded' %}bg-red-500{% elif global_budget.status == 'warning' %}bg-yellow-500{% else %}bg-green-500{% endif %}"
                 style="width: {{ [global_budget.pct_used, 100] | min }}%"></div>
        </div>
        <span class="text-sm text-gray-400 whitespace-nowrap">
            ${{ "%.2f"|format(global_budget.today_spend) }} / ${{ "%.2f"|format(global_budget.daily_limit) }} ({{ "%.0f"|format(global_budget.pct_used) }}%)
        </span>
    </div>
    {% else %}
    <p class="text-gray-500 text-sm">No global budget set. <a href="/dashboard/budgets" class="text-blue-400 hover:underline">Create one</a></p>
    {% endif %}
</div>

<!-- Active Combos -->
<div class="bg-gray-800 rounded-xl p-6 border border-gray-700">
    <h2 class="text-lg font-semibold text-white mb-4">Active Combos</h2>
    {% if combos %}
    <div class="space-y-3">
        {% for name, models in combos.items() %}
        <div class="bg-gray-900 rounded-lg p-4 border border-gray-700">
            <div class="flex items-center justify-between mb-2">
                <span class="text-white font-medium">{{ name }}</span>
                <span class="text-gray-500 text-sm">{{ models|length }} models</span>
            </div>
            <div class="flex flex-wrap items-center gap-2">
                {% for model in models %}
                <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-blue-900 text-blue-200">{{ model }}</span>
                {% if not loop.last %}
                <span class="text-gray-500 text-sm">→</span>
                {% endif %}
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <p class="text-gray-500 text-sm">No combos configured.</p>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: Update usage template to add cost column**

Replace `src/janus/dashboard/templates/usage.html`:

```html
{% extends "base.html" %}
{% block title %}Usage - Janus{% endblock %}
{% block usage_active %}bg-gray-700 text-white{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Usage</h1>

<!-- Stats Cards -->
<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Total Requests</p>
        <p class="text-3xl font-bold text-white">{{ stats.total_requests }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Input Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(stats.total_input_tokens) }}</p>
    </div>
    <div class="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <p class="text-gray-400 text-sm font-medium mb-1">Output Tokens</p>
        <p class="text-3xl font-bold text-white">{{ "{:,}".format(stats.total_output_tokens) }}</p>
    </div>
</div>

<!-- Usage by Model -->
<div class="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
    <div class="px-6 py-4 border-b border-gray-700">
        <h2 class="text-lg font-semibold text-white">Usage by Model</h2>
    </div>
    {% if stats.by_model %}
    <table class="w-full">
        <thead>
            <tr class="bg-gray-900 border-b border-gray-700">
                <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Model</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Requests</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Input Tokens</th>
                <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">Output Tokens</th>
            </tr>
        </thead>
        <tbody class="divide-y divide-gray-700">
            {% for row in stats.by_model %}
            <tr class="hover:bg-gray-750">
                <td class="px-4 py-3 text-sm text-white font-mono">{{ row.model or '—' }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ row.requests }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ "{:,}".format(row.input_tokens) }}</td>
                <td class="px-4 py-3 text-sm text-gray-300 text-right">{{ "{:,}".format(row.output_tokens) }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="px-6 py-12 text-center">
        <p class="text-gray-400">No usage data yet.</p>
    </div>
    {% endif %}
</div>
<p class="text-gray-500 text-xs mt-4">Cost analytics available on the <a href="/dashboard/analytics" class="text-blue-400 hover:underline">Analytics page</a>.</p>
{% endblock %}
```

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/routes.py src/janus/dashboard/templates/overview.html src/janus/dashboard/templates/usage.html
git commit -m "feat: enhanced overview with today's spend + budget bar, usage page link to analytics"
```

---

## Task 19: CLI — Budgets Sub-App

**Files:**
- Modify: `src/janus/cli.py`

- [ ] **Step 1: Add budgets sub-app to CLI**

In `src/janus/cli.py`, after the `usage_app` definition and registration (after line 68), add:

```python
budgets_app = typer.Typer(help="Manage spending budgets")
pricing_app = typer.Typer(help="View model pricing")
app.add_typer(budgets_app, name="budgets")
app.add_typer(pricing_app, name="pricing")
```

After the `usage_stats` function (end of file), add:

```python
@budgets_app.command("list")
def budgets_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """List all active budgets."""
    import asyncio

    from janus.storage.budgets import get_budget_status, get_budgets
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    budgets = asyncio.run(get_budgets(db_path))
    if not budgets:
        typer.echo("No budgets found.")
        return
    for b in budgets:
        status = asyncio.run(get_budget_status(db_path, key_id=b["key_id"]))
        scope = f"Key #{b['key_id']}" if b["key_id"] else "Global"
        spend_str = f"${status['today_spend']:.2f}" if status else "—"
        limit_str = f"${b['daily_limit']:.2f}"
        pct_str = f"{status['pct_used']:.0f}%" if status else "—"
        st = status["status"] if status else "—"
        typer.echo(
            f"  {b['id']:>3}  {scope:<15}  {limit_str:>10}  "
            f"spent: {spend_str:>10}  {pct_str:>6}  {st}"
        )


@budgets_app.command("set")
def budgets_set(
    daily: float = typer.Option(..., "--daily", "-d", help="Daily limit in USD"),
    key: str = typer.Option("global", "--key", "-k", help="Key name or 'global'"),
    warn: float = typer.Option(80, "--warn", "-w", help="Warn threshold percentage"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Create or update a budget."""
    import asyncio

    from janus.storage.api_keys import list_keys
    from janus.storage.budgets import create_or_update_budget
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))

    key_id: int | None = None
    if key != "global":
        keys = asyncio.run(list_keys(db_path))
        match = next((k for k in keys if k["name"] == key), None)
        if match is None:
            typer.echo(f"Key '{key}' not found.")
            raise typer.Exit(1)
        key_id = match["id"]

    budget_id = asyncio.run(
        create_or_update_budget(db_path, key_id=key_id, daily_limit=daily, warn_pct=warn)
    )
    scope = key if key == "global" else f"key '{key}'"
    typer.echo(f"Budget {budget_id} set: {scope} daily limit = ${daily:.2f}, warn at {warn:.0f}%")


@budgets_app.command("delete")
def budgets_delete(
    budget_id: int = typer.Argument(..., help="Budget ID to delete"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Delete a budget."""
    import asyncio

    from janus.storage.budgets import delete_budget
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    deleted = asyncio.run(delete_budget(db_path, budget_id))
    if deleted:
        typer.echo(f"Deleted budget {budget_id}")
    else:
        typer.echo(f"Budget {budget_id} not found")
        raise typer.Exit(1)
```

- [ ] **Step 2: Verify CLI loads**

Run: `.venv/bin/janus budgets --help`
Expected: Shows `list`, `set`, `delete` commands

- [ ] **Step 3: Commit**

```bash
git add src/janus/cli.py
git commit -m "feat: CLI budgets sub-app (list/set/delete)"
```

---

## Task 20: CLI — Pricing Sub-App

**Files:**
- Modify: `src/janus/cli.py`

- [ ] **Step 1: Add pricing sub-app commands**

After the `budgets_delete` function added in Task 19, add:

```python
@pricing_app.command("list")
def pricing_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """List all known model pricing."""
    from janus.config.loader import load_config
    from janus.pricing.registry import PricingRegistry

    cfg = load_config(Path(config).expanduser())
    reg = PricingRegistry(cfg.pricing)
    all_pricing = reg.get_all()
    for model in sorted(all_pricing.keys()):
        p = all_pricing[model]
        typer.echo(
            f"  {model:<40}  "
            f"in: ${p.input_per_mtok:<6}  "
            f"out: ${p.output_per_mtok:<6}  "
            f"cc: ${p.cache_creation_per_mtok:<6}  "
            f"cr: ${p.cache_read_per_mtok:<6}"
        )


@pricing_app.command("show")
def pricing_show(
    model: str = typer.Argument(..., help="Model name"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show pricing for a specific model."""
    from janus.config.loader import load_config
    from janus.pricing.registry import PricingRegistry

    cfg = load_config(Path(config).expanduser())
    reg = PricingRegistry(cfg.pricing)
    p = reg.get(model)
    if p is None:
        typer.echo(f"No pricing found for '{model}'")
        raise typer.Exit(1)
    typer.echo(f"Model: {model}")
    typer.echo(f"  Input:              ${p.input_per_mtok} / Mtok")
    typer.echo(f"  Output:             ${p.output_per_mtok} / Mtok")
    typer.echo(f"  Cache creation:     ${p.cache_creation_per_mtok} / Mtok")
    typer.echo(f"  Cache read:         ${p.cache_read_per_mtok} / Mtok")
```

- [ ] **Step 2: Verify CLI loads**

Run: `.venv/bin/janus pricing --help`
Expected: Shows `list`, `show` commands

- [ ] **Step 3: Commit**

```bash
git add src/janus/cli.py
git commit -m "feat: CLI pricing sub-app (list/show)"
```

---

## Task 21: CLI — Enhanced Usage Commands

**Files:**
- Modify: `src/janus/cli.py`

- [ ] **Step 1: Add cost and by-key commands to usage sub-app**

After the existing `usage_stats` function, add:

```python
@usage_app.command("cost")
def usage_cost(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to show"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show cost breakdown by model."""
    import asyncio

    from janus.storage.analytics import get_breakdown
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    rows = asyncio.run(get_breakdown(db_path, dimension="model", days=days))
    if not rows:
        typer.echo("No usage data.")
        return
    total_cost = sum(r["cost"] for r in rows)
    typer.echo(f"Cost breakdown (last {days} days):")
    typer.echo(f"{'Model':<35} {'Requests':>8} {'Cost':>12}")
    typer.echo("-" * 58)
    for r in rows:
        typer.echo(f"  {r['model'] or '—':<33} {r['requests']:>8} ${r['cost']:>10.4f}")
    typer.echo("-" * 58)
    typer.echo(f"  {'Total':<33} {'':>8} ${total_cost:>10.4f}")


@usage_app.command("by-key")
def usage_by_key(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to show"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show spending per client API key."""
    import asyncio

    from janus.storage.analytics import get_breakdown
    from janus.storage.api_keys import list_keys
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    rows = asyncio.run(get_breakdown(db_path, dimension="client_key", days=days))
    keys = asyncio.run(list_keys(db_path))
    key_map = {k["id"]: k["name"] for k in keys}
    if not rows:
        typer.echo("No per-key usage data.")
        return
    typer.echo(f"Spending per key (last {days} days):")
    typer.echo(f"{'Key':<25} {'Requests':>8} {'Cost':>12}")
    typer.echo("-" * 48)
    for r in rows:
        name = key_map.get(r["client_key_id"], f"Key #{r['client_key_id']}") if r["client_key_id"] else "No key"
        typer.echo(f"  {name:<23} {r['requests']:>8} ${r['cost']:>10.4f}")
```

- [ ] **Step 2: Verify CLI loads**

Run: `.venv/bin/janus usage --help`
Expected: Shows `stats`, `cost`, `by-key` commands

- [ ] **Step 3: Commit**

```bash
git add src/janus/cli.py
git commit -m "feat: CLI usage cost and by-key commands"
```

---

## Task 22: Lint, Typecheck, Full Test Suite

**Files:**
- All files

- [ ] **Step 1: Run ruff lint**

Run: `.venv/bin/ruff check src/janus/ tests/`
Expected: No errors. Fix any issues found.

- [ ] **Step 2: Run ruff format check**

Run: `.venv/bin/ruff format --check src/janus/ tests/`
Expected: No errors. Fix any issues found.

- [ ] **Step 3: Run mypy strict**

Run: `.venv/bin/mypy src/janus/`
Expected: No errors. Fix any type issues.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: All tests pass

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: lint and typecheck cleanup for Phase 6"
```

---

## Task 23: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add Phase 6 sections to AGENTS.md**

Add the following sections to `AGENTS.md` (before the Config section):

```markdown
## Pricing & cost tracking

The `pricing/` package provides per-model cost estimation. `PricingRegistry` merges builtin defaults (~30 popular models) with YAML overrides from the `pricing:` config section. Cost is computed at recording time via `compute_cost(usage, model, registry)` and stored in the `usage.cost` column. Unknown models cost $0.0 (not an error).

- `pricing/builtin.py` — hardcoded `dict[str, ModelPricing]` seed data ($ per million tokens: input, output, cache_creation, cache_read).
- `pricing/registry.py` — `PricingRegistry(overrides)`, does exact match then progressively shorter prefix matching for model variants.
- `pricing/calculator.py` — `compute_cost(usage, model, registry) -> float`, pure function.
- Config: `pricing:` section in YAML, same dict structure as builtin.

## Budget enforcement

Budgets are daily spending limits stored in the `budgets` SQLite table. Each budget targets either a specific API key (`key_id`) or is global (`key_id = NULL`). Enforcement happens in `_handle()` before routing:

- **Warn threshold** (default 80%): request proceeds, dashboard shows amber.
- **Hard threshold** (100%): request rejected with `429` + `Retry-After` header.
- Both per-key and global budgets are checked; most restrictive wins.
- Fail-safe: DB errors don't block requests.
- `storage/budgets.py` — CRUD + `get_budget_status(key_id)`.
- CLI: `janus budgets list/set/delete`.

## Analytics

`storage/analytics.py` provides aggregated queries: `get_spend_summary(days)`, `get_breakdown(dimension, days)`, `get_success_rate(days)`. The dashboard `/dashboard/analytics` page uses Chart.js (via CDN) for spend trends and success-rate donut charts. Breakdowns available by model, provider, account, or client key.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md for Phase 6 (pricing, budgets, analytics)"
```

---

## Task 24: Final Integration Test Run + Push

- [ ] **Step 1: Run the complete test suite one final time**

Run: `.venv/bin/python -m pytest -v`
Expected: All tests pass, no skips on new tests

- [ ] **Step 2: Run lint + typecheck one final time**

Run: `.venv/bin/ruff check src/janus/ tests/ && .venv/bin/mypy src/janus/`
Expected: Clean

- [ ] **Step 3: Verify CLI commands work end-to-end**

Run:
```bash
.venv/bin/janus pricing list
.venv/bin/janus pricing show gpt-4o
.venv/bin/janus budgets --help
.venv/bin/janus usage --help
```
Expected: All commands produce output without errors

- [ ] **Step 4: Push to main**

```bash
git push origin main
```
