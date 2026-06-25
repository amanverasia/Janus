# Phase 9: Full Dashboard CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Janus dashboard from read-only to full CLI parity — providers, combos, token savers, pricing, and settings all manageable from the web UI.

**Architecture:** Config moves from YAML to SQLite (YAML becomes a one-time seed). New storage modules handle CRUD. Dashboard routes gain HTMX endpoints for all operations. Hot-reload helpers rebuild in-memory state after changes. Provider catalog offers pre-built templates for ~15 known providers.

**Tech Stack:** FastAPI, Jinja2, HTMX, Tailwind CDN, Sortable.js (CDN), aiosqlite, Chart.js (existing).

---

## File Structure

**New storage modules:**
- `src/janus/storage/settings.py` — key-value settings store (`get_setting`, `set_setting`, `get_all_settings`)
- `src/janus/storage/providers_db.py` — provider CRUD (list, get, create, update, delete, toggle)
- `src/janus/storage/combos_db.py` — combo CRUD
- `src/janus/storage/pricing_db.py` — pricing override CRUD

**New dashboard modules:**
- `src/janus/dashboard/catalog.py` — provider catalog (~15 known providers)
- `src/janus/dashboard/reload.py` — hot-reload helpers (`reload_providers`, `reload_combos`, `reload_savers`, `reload_pricing`)

**Modified files:**
- `src/janus/storage/database.py` — new tables in `_SCHEMA`, new `_seed_from_config` function
- `src/janus/app.py` — load from DB after seed, expose reload helpers on `app.state`
- `src/janus/dashboard/routes.py` — all new routes + modify existing provider/combo pages
- `src/janus/dashboard/templates/base.html` — grouped sidebar nav
- `src/janus/dashboard/templates/providers.html` — rich card grid with CRUD buttons
- `src/janus/dashboard/templates/combos.html` — combo editor with drag-drop

**New templates:**
- `providers_partial.html` — HTMX partial for provider card grid
- `providers_form.html` — add/edit provider modal
- `combos_partial.html` — HTMX partial for combo list
- `combos_form.html` — add/edit combo modal
- `savers.html` — token saver toggle page
- `tools.html` — CLI tool setup page
- `pricing.html` — pricing table + overrides
- `settings.html` — server settings form

**New tests:**
- `tests/unit/storage/test_settings.py`
- `tests/unit/storage/test_providers_db.py`
- `tests/unit/storage/test_combos_db.py`
- `tests/unit/storage/test_pricing_db.py`
- `tests/integration/test_dashboard_crud.py`
- `tests/integration/test_yaml_migration.py`

---

### Task 1: DB Schema — New Tables

**Files:**
- Modify: `src/janus/storage/database.py`

- [ ] **Step 1: Add new tables to `_SCHEMA`**

In `src/janus/storage/database.py`, add these tables to the `_SCHEMA` string, before the indexes:

```sql

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT,
    models TEXT NOT NULL DEFAULT '[]',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    models TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_overrides (
    model TEXT PRIMARY KEY,
    input_per_mtok REAL NOT NULL,
    output_per_mtok REAL NOT NULL,
    cache_creation_per_mtok REAL NOT NULL DEFAULT 0.0,
    cache_read_per_mtok REAL NOT NULL DEFAULT 0.0
);
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All 180 tests pass (tables are created but unused)

- [ ] **Step 3: Commit**

```bash
git add src/janus/storage/database.py
git commit -m "feat: add providers/combos/settings/pricing tables to DB schema"
```

---

### Task 2: Settings Storage Module

**Files:**
- Create: `src/janus/storage/settings.py`
- Test: `tests/unit/storage/test_settings.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/storage/test_settings.py`:

```python
import pytest

from janus.storage.database import get_connection, init_db
from janus.storage.settings import get_all_settings, get_setting, set_setting


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_set_and_get_setting(db):
    await set_setting(db, "foo", "bar")
    assert await get_setting(db, "foo") == "bar"


async def test_get_setting_default(db):
    assert await get_setting(db, "nonexistent", "default_val") == "default_val"


async def test_get_setting_none_if_no_default(db):
    assert await get_setting(db, "nonexistent") is None


async def test_set_setting_overwrites(db):
    await set_setting(db, "key", "v1")
    await set_setting(db, "key", "v2")
    assert await get_setting(db, "key") == "v2"


async def test_get_all_settings_empty(db):
    assert await get_all_settings(db) == {}


async def test_get_all_settings(db):
    await set_setting(db, "a", "1")
    await set_setting(db, "b", "2")
    result = await get_all_settings(db)
    assert result == {"a": "1", "b": "2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_settings.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Create `src/janus/storage/settings.py`:

```python
from __future__ import annotations

from pathlib import Path

from .database import get_connection


async def get_setting(db_path: str | Path, key: str, default: str | None = None) -> str | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(db_path: str | Path, key: str, value: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_all_settings(db_path: str | Path) -> dict[str, str]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    return {row["key"]: row["value"] for row in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_settings.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/settings.py tests/unit/storage/test_settings.py
git commit -m "feat: add settings key-value storage module"
```

---

### Task 3: Provider DB Storage Module

**Files:**
- Create: `src/janus/storage/providers_db.py`
- Test: `tests/unit/storage/test_providers_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/storage/test_providers_db.py`:

```python
import json

import pytest

from janus.storage.database import init_db
from janus.storage.providers_db import (
    create_provider,
    delete_provider,
    get_provider,
    list_providers,
    toggle_provider,
    update_provider,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_provider(db):
    await create_provider(db, {
        "id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-xxx",
        "models": ["gpt-4o", "gpt-4o-mini"],
    })
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "openai"
    assert providers[0]["is_enabled"] == 1
    assert json.loads(providers[0]["models"]) == ["gpt-4o", "gpt-4o-mini"]


async def test_get_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    p = await get_provider(db, "test")
    assert p["id"] == "test"
    assert p["api_key"] is None


async def test_get_provider_not_found(db):
    assert await get_provider(db, "nonexistent") is None


async def test_update_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://old.local",
        "api_key": "old",
        "models": ["m1"],
    })
    await update_provider(db, "test", {
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://new.local",
        "api_key": "new",
        "models": ["m1", "m2"],
    })
    p = await get_provider(db, "test")
    assert p["base_url"] == "https://new.local"
    assert json.loads(p["models"]) == ["m1", "m2"]


async def test_toggle_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 0
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 1


async def test_delete_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    await delete_provider(db, "test")
    assert await get_provider(db, "test") is None


async def test_list_providers_only_enabled(db):
    await create_provider(db, {
        "id": "a",
        "prefix": "a",
        "api_type": "openai_compat",
        "base_url": "https://a.local",
        "api_key": None,
        "models": [],
    })
    await create_provider(db, {
        "id": "b",
        "prefix": "b",
        "api_type": "openai_compat",
        "base_url": "https://b.local",
        "api_key": None,
        "models": [],
    })
    await toggle_provider(db, "b")
    enabled = await list_providers(db, enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0]["id"] == "a"
    all_p = await list_providers(db, enabled_only=False)
    assert len(all_p) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_providers_db.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Create `src/janus/storage/providers_db.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import get_connection


async def list_providers(
    db_path: str | Path, enabled_only: bool = False
) -> list[dict[str, Any]]:
    query = "SELECT * FROM providers"
    if enabled_only:
        query += " WHERE is_enabled = 1"
    query += " ORDER BY id"
    async with get_connection(db_path) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_provider(db_path: str | Path, provider_id: str) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_provider(db_path: str | Path, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO providers (id, prefix, api_type, base_url, api_key, models)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["prefix"],
                data["api_type"],
                data["base_url"],
                data.get("api_key"),
                json.dumps(data.get("models", [])),
            ),
        )
        await db.commit()


async def update_provider(db_path: str | Path, provider_id: str, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """UPDATE providers SET prefix = ?, api_type = ?, base_url = ?,
               api_key = ?, models = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                data["prefix"],
                data["api_type"],
                data["base_url"],
                data.get("api_key"),
                json.dumps(data.get("models", [])),
                provider_id,
            ),
        )
        await db.commit()


async def toggle_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE providers SET is_enabled = 1 - is_enabled, updated_at = datetime('now') WHERE id = ?",
            (provider_id,),
        )
        await db.commit()


async def delete_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_providers_db.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/providers_db.py tests/unit/storage/test_providers_db.py
git commit -m "feat: add provider DB storage module"
```

---

### Task 4: Combo DB Storage Module

**Files:**
- Create: `src/janus/storage/combos_db.py`
- Test: `tests/unit/storage/test_combos_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/storage/test_combos_db.py`:

```python
import json

import pytest

from janus.storage.combos_db import (
    create_combo,
    delete_combo,
    get_combo,
    list_combos,
    update_combo,
)
from janus.storage.database import init_db


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_combo(db):
    await create_combo(db, {"name": "best-effort", "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]})
    combos = await list_combos(db)
    assert len(combos) == 1
    assert combos[0]["name"] == "best-effort"
    assert json.loads(combos[0]["models"]) == ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]


async def test_get_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    c = await get_combo(db, 1)
    assert c["name"] == "test"


async def test_get_combo_not_found(db):
    assert await get_combo(db, 999) is None


async def test_update_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    await update_combo(db, 1, {"name": "test", "models": ["a/b", "c/d"]})
    c = await get_combo(db, 1)
    assert json.loads(c["models"]) == ["a/b", "c/d"]


async def test_delete_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    await delete_combo(db, 1)
    assert await get_combo(db, 1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_combos_db.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Create `src/janus/storage/combos_db.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import get_connection


async def list_combos(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM combos ORDER BY name") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_combo(db_path: str | Path, combo_id: int) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM combos WHERE id = ?", (combo_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_combo(db_path: str | Path, data: dict[str, Any]) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO combos (name, models) VALUES (?, ?)",
            (data["name"], json.dumps(data["models"])),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_combo(db_path: str | Path, combo_id: int, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE combos SET name = ?, models = ?, updated_at = datetime('now') WHERE id = ?",
            (data["name"], json.dumps(data["models"]), combo_id),
        )
        await db.commit()


async def delete_combo(db_path: str | Path, combo_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM combos WHERE id = ?", (combo_id,))
        await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_combos_db.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/combos_db.py tests/unit/storage/test_combos_db.py
git commit -m "feat: add combo DB storage module"
```

---

### Task 5: Pricing Override DB Storage Module

**Files:**
- Create: `src/janus/storage/pricing_db.py`
- Test: `tests/unit/storage/test_pricing_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/storage/test_pricing_db.py`:

```python
import pytest

from janus.storage.database import init_db
from janus.storage.pricing_db import (
    create_or_update_pricing_override,
    delete_pricing_override,
    get_pricing_overrides,
    list_pricing_overrides,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_override(db):
    await create_or_update_pricing_override(db, {
        "model": "custom-model",
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
        "cache_creation_per_mtok": 0.5,
        "cache_read_per_mtok": 0.1,
    })
    overrides = await list_pricing_overrides(db)
    assert len(overrides) == 1
    assert overrides[0]["model"] == "custom-model"
    assert overrides[0]["input_per_mtok"] == 1.0


async def test_update_override(db):
    await create_or_update_pricing_override(db, {
        "model": "m",
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
        "cache_creation_per_mtok": 0.0,
        "cache_read_per_mtok": 0.0,
    })
    await create_or_update_pricing_override(db, {
        "model": "m",
        "input_per_mtok": 5.0,
        "output_per_mtok": 10.0,
        "cache_creation_per_mtok": 0.0,
        "cache_read_per_mtok": 0.0,
    })
    overrides = await list_pricing_overrides(db)
    assert len(overrides) == 1
    assert overrides[0]["input_per_mtok"] == 5.0


async def test_delete_override(db):
    await create_or_update_pricing_override(db, {
        "model": "m",
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
        "cache_creation_per_mtok": 0.0,
        "cache_read_per_mtok": 0.0,
    })
    await delete_pricing_override(db, "m")
    assert await list_pricing_overrides(db) == []


async def test_get_overrides_as_dict(db):
    await create_or_update_pricing_override(db, {
        "model": "m1",
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
        "cache_creation_per_mtok": 0.0,
        "cache_read_per_mtok": 0.0,
    })
    result = await get_pricing_overrides(db)
    assert "m1" in result
    assert result["m1"]["input_per_mtok"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_pricing_db.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Create `src/janus/storage/pricing_db.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def list_pricing_overrides(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM pricing_overrides ORDER BY model") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_pricing_overrides(db_path: str | Path) -> dict[str, dict[str, float]]:
    rows = await list_pricing_overrides(db_path)
    return {
        row["model"]: {
            "input_per_mtok": row["input_per_mtok"],
            "output_per_mtok": row["output_per_mtok"],
            "cache_creation_per_mtok": row["cache_creation_per_mtok"],
            "cache_read_per_mtok": row["cache_read_per_mtok"],
        }
        for row in rows
    }


async def create_or_update_pricing_override(db_path: str | Path, data: dict[str, float | str]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO pricing_overrides
               (model, input_per_mtok, output_per_mtok, cache_creation_per_mtok, cache_read_per_mtok)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(model) DO UPDATE SET
               input_per_mtok = excluded.input_per_mtok,
               output_per_mtok = excluded.output_per_mtok,
               cache_creation_per_mtok = excluded.cache_creation_per_mtok,
               cache_read_per_mtok = excluded.cache_read_per_mtok""",
            (
                str(data["model"]),
                float(data["input_per_mtok"]),
                float(data["output_per_mtok"]),
                float(data.get("cache_creation_per_mtok", 0.0)),
                float(data.get("cache_read_per_mtok", 0.0)),
            ),
        )
        await db.commit()


async def delete_pricing_override(db_path: str | Path, model: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM pricing_overrides WHERE model = ?", (model,))
        await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_pricing_db.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/pricing_db.py tests/unit/storage/test_pricing_db.py
git commit -m "feat: add pricing override DB storage module"
```

---

### Task 6: YAML Seed Logic

**Files:**
- Modify: `src/janus/storage/database.py`
- Test: `tests/integration/test_yaml_migration.py`

- [ ] **Step 1: Write failing tests**

Create `tests/integration/test_yaml_migration.py`:

```python
import json

import pytest

from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from janus.storage.database import init_db
from janus.storage.providers_db import list_providers
from janus.storage.combos_db import list_combos
from janus.storage.settings import get_all_settings, get_setting
from janus.storage.pricing_db import get_pricing_overrides


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_seed_providers_from_config(db):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[
            ProviderConfig(id="openai", prefix="openai", api_type="openai_compat", base_url="https://api.openai.com/v1", api_key="sk-x", models=["gpt-4o"]),
        ],
    )
    await seed_from_config(db, config)
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "openai"
    assert json.loads(providers[0]["models"]) == ["gpt-4o"]


async def test_seed_combos_from_config(db):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        combos=[ComboConfig(name="best", models=["openai/gpt-4o"])],
    )
    await seed_from_config(db, config)
    combos = await list_combos(db)
    assert len(combos) == 1
    assert combos[0]["name"] == "best"


async def test_seed_saver_settings(db):
    from janus.storage.database import seed_from_config

    config = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    config.token_savers.rtk.enabled = True
    config.token_savers.caveman.enabled = False
    config.token_savers.ponytail.enabled = True
    config.token_savers.ponytail.level = "ultra"
    await seed_from_config(db, config)
    assert await get_setting(db, "saver_rtk_enabled") == "true"
    assert await get_setting(db, "saver_caveman_enabled") == "false"
    assert await get_setting(db, "saver_ponytail_enabled") == "true"
    assert await get_setting(db, "saver_ponytail_level") == "ultra"


async def test_seed_pricing(db):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        pricing={"custom-model": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    )
    await seed_from_config(db, config)
    overrides = await get_pricing_overrides(db)
    assert "custom-model" in overrides


async def test_seed_skips_if_data_exists(db):
    from janus.storage.database import seed_from_config
    from janus.storage.providers_db import create_provider

    await create_provider(db, {
        "id": "existing",
        "prefix": "existing",
        "api_type": "openai_compat",
        "base_url": "https://existing.local",
        "api_key": None,
        "models": [],
    })
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[
            ProviderConfig(id="new", prefix="new", api_type="openai_compat", base_url="https://new.local", api_key=None, models=[]),
        ],
    )
    await seed_from_config(db, config)
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "existing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_yaml_migration.py -v`
Expected: FAIL (seed_from_config not found)

- [ ] **Step 3: Write the implementation**

Add to `src/janus/storage/database.py` (at the end of the file):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from janus.config.schema import JanusConfig


async def _table_is_empty(db: aiosqlite.Connection, table: str) -> bool:
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        row = await cur.fetchone()
    return row[0] == 0


async def seed_from_config(db_path: str | Path, config: JanusConfig) -> None:
    import json

    from janus.config.schema import JanusConfig  # noqa: F811

    async with get_connection(db_path) as db:
        if await _table_is_empty(db, "providers") and config.providers:
            for pc in config.providers:
                await db.execute(
                    """INSERT INTO providers (id, prefix, api_type, base_url, api_key, models)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (pc.id, pc.prefix, pc.api_type, pc.base_url, pc.api_key, json.dumps(pc.models)),
                )

        if await _table_is_empty(db, "combos") and config.combos:
            for combo in config.combos:
                await db.execute(
                    "INSERT INTO combos (name, models) VALUES (?, ?)",
                    (combo.name, json.dumps(combo.models)),
                )

        if await _table_is_empty(db, "settings"):
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("saver_rtk_enabled", "true" if config.token_savers.rtk.enabled else "false"),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("saver_caveman_enabled", "true" if config.token_savers.caveman.enabled else "false"),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("saver_ponytail_enabled", "true" if config.token_savers.ponytail.enabled else "false"),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("saver_ponytail_level", config.token_savers.ponytail.level),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("server_require_api_key", "true" if config.server.require_api_key else "false"),
            )

        if await _table_is_empty(db, "pricing_overrides") and config.pricing:
            for model, rates in config.pricing.items():
                await db.execute(
                    """INSERT INTO pricing_overrides
                       (model, input_per_mtok, output_per_mtok, cache_creation_per_mtok, cache_read_per_mtok)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        model,
                        rates.get("input_per_mtok", 0.0),
                        rates.get("output_per_mtok", 0.0),
                        rates.get("cache_creation_per_mtok", 0.0),
                        rates.get("cache_read_per_mtok", 0.0),
                    ),
                )

        await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_yaml_migration.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/database.py tests/integration/test_yaml_migration.py
git commit -m "feat: add seed_from_config for YAML → DB migration"
```

---

### Task 7: App.py — Load from DB + Reload Helpers

**Files:**
- Modify: `src/janus/app.py`
- Create: `src/janus/dashboard/reload.py`

- [ ] **Step 1: Create reload helpers module**

Create `src/janus/dashboard/reload.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from janus.app import _build_provider
from janus.config.schema import ComboConfig, ProviderConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.combos_db import list_combos
from janus.storage.pricing_db import get_pricing_overrides
from janus.storage.providers_db import list_providers
from janus.storage.settings import get_all_settings
from janus.tokensavers.base import TokenSaver
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.ponytail import PonytailSaver
from janus.tokensavers.rtk import RTKSaver


async def reload_providers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_providers(db_path, enabled_only=True)

    old_providers: dict[str, Provider] = app.state.providers

    registry = ProviderRegistry()
    new_providers: dict[str, Provider] = {}

    for row in rows:
        models = json.loads(row["models"]) if row["models"] else []
        pc = ProviderConfig(
            id=row["id"],
            prefix=row["prefix"],
            api_type=row["api_type"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            models=models,
        )
        registry.register(pc)
        if row["id"] in old_providers and pc == app.state.registry.providers.get(pc.prefix, [None])[0]:
            new_providers[row["id"]] = old_providers[row["id"]]
        else:
            new_providers[row["id"]] = _build_provider(pc)

    for old_id, old_provider in old_providers.items():
        if old_id not in new_providers:
            await old_provider.close()

    app.state.providers = new_providers
    app.state.registry = registry
    app.state.fallback_handler = FallbackHandler(registry)


async def reload_combos(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_combos(db_path)
    registry: ProviderRegistry = app.state.registry
    registry._combos = {}
    for row in rows:
        models = json.loads(row["models"]) if row["models"] else []
        registry.register_combo(ComboConfig(name=row["name"], models=models))


async def reload_savers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    settings = await get_all_settings(db_path)
    savers: list[TokenSaver] = []
    if settings.get("saver_rtk_enabled", "true").lower() == "true":
        savers.append(RTKSaver())
    if settings.get("saver_caveman_enabled", "false").lower() == "true":
        savers.append(CavemanSaver())
    if settings.get("saver_ponytail_enabled", "false").lower() == "true":
        level = settings.get("saver_ponytail_level", "full")
        savers.append(PonytailSaver(level=level))
    app.state.saver_pipeline = SaverPipeline(savers)


async def reload_pricing(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    overrides = await get_pricing_overrides(db_path)
    app.state.pricing_registry = PricingRegistry(overrides)
```

- [ ] **Step 2: Modify `app.py` `create_app()` to seed and load from DB**

Replace the body of `create_app()` in `src/janus/app.py` (lines 47-85) with:

```python
def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0", lifespan=lifespan)
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()
    app.state.registry = registry
    app.state.config = config
    app.state.db_path = config.server.data_dir / "janus.db"
    app.state.fallback_handler = FallbackHandler(registry)
    app.state.saver_pipeline = SaverPipeline([])
    app.state.pricing_registry = PricingRegistry(config.pricing)
    app.state.providers: dict[str, Provider] = {}
    app.include_router(router, prefix="/v1")

    from janus.dashboard.routes import router as dashboard_router

    app.include_router(dashboard_router, prefix="/dashboard")
    return app
```

Then modify `lifespan()` to seed and load from DB on startup:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    await init_db(db_path)

    config: JanusConfig = app.state.config
    await seed_from_config(db_path, config)

    from janus.dashboard.reload import reload_combos, reload_pricing, reload_providers, reload_savers

    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)

    yield
    for provider in app.state.providers.values():
        await provider.close()
```

Add import at top of `app.py`:
```python
from janus.storage.database import init_db, seed_from_config
```

- [ ] **Step 3: Update existing tests to work with new create_app**

The existing tests in `test_dashboard.py` and `test_api.py` pass a `ProviderRegistry` and `JanusConfig` directly to `create_app()`. With the new flow, providers are loaded from DB during lifespan. Since ASGITransport tests don't run lifespan, the `_ensure_db` pattern in dashboard routes handles DB init. But we need to also seed and load from DB there.

Update `_ensure_db` in `dashboard/routes.py` to also seed and load:

```python
async def _ensure_db(request: Request) -> Path:
    db_path = Path(request.app.state.db_path)
    if not getattr(request.app.state, "_dashboard_db_ready", False):
        await init_db(db_path)
        from janus.dashboard.reload import reload_combos, reload_pricing, reload_providers, reload_savers

        await reload_providers(request.app)
        await reload_combos(request.app)
        await reload_savers(request.app)
        await reload_pricing(request.app)
        request.app.state._dashboard_db_ready = True
    return db_path
```

The existing `test_dashboard.py` fixture passes a registry with providers pre-registered. After this change, `create_app()` no longer reads from the registry for providers — it reads from DB. The test fixture needs updating:

Update `tests/integration/test_dashboard.py` fixture to use config instead of registry:

```python
@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t",
                prefix="t",
                api_type="openai_compat",
                base_url="https://test.local/v1",
                api_key="k",
                models=["m1"],
            ),
        ],
        combos=[ComboConfig(name="stk", models=["t/m1"])],
    )
    return create_app(config=cfg)
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass (may need minor fixes to fixtures that relied on old `create_app(reg, cfg)` pattern)

- [ ] **Step 5: Commit**

```bash
git add src/janus/app.py src/janus/dashboard/reload.py src/janus/dashboard/routes.py tests/
git commit -m "feat: load config from DB with hot-reload helpers"
```

---

### Task 8: Provider Catalog

**Files:**
- Create: `src/janus/dashboard/catalog.py`

- [ ] **Step 1: Write the provider catalog**

Create `src/janus/dashboard/catalog.py`:

```python
from __future__ import annotations

from typing import Any

CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI",
        "icon": "🟢",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "prefix": "openai",
        "default_models": ["gpt-4o", "gpt-4o-mini", "o3", "o4-mini"],
    },
    "anthropic": {
        "name": "Anthropic",
        "icon": "🟠",
        "api_type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "prefix": "anthropic",
        "default_models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
    },
    "gemini": {
        "name": "Google Gemini",
        "icon": "🔵",
        "api_type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "prefix": "gemini",
        "default_models": ["gemini-2.5-pro", "gemini-2.0-flash"],
    },
    "groq": {
        "name": "Groq",
        "icon": "⚡",
        "api_type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "prefix": "groq",
        "default_models": ["llama-3.3-70b-instruct"],
    },
    "together": {
        "name": "Together AI",
        "icon": "🤝",
        "api_type": "openai_compat",
        "base_url": "https://api.together.xyz/v1",
        "prefix": "together",
        "default_models": [],
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "🔬",
        "api_type": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "prefix": "deepseek",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "icon": "🔀",
        "api_type": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "prefix": "openrouter",
        "default_models": [],
    },
    "mistral": {
        "name": "Mistral",
        "icon": "🌬️",
        "api_type": "openai_compat",
        "base_url": "https://api.mistral.ai/v1",
        "prefix": "mistral",
        "default_models": ["mistral-large-2411"],
    },
    "fireworks": {
        "name": "Fireworks",
        "icon": "🎆",
        "api_type": "openai_compat",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "prefix": "fireworks",
        "default_models": [],
    },
    "perplexity": {
        "name": "Perplexity",
        "icon": "🔍",
        "api_type": "openai_compat",
        "base_url": "https://api.perplexity.ai",
        "prefix": "perplexity",
        "default_models": [],
    },
    "xai": {
        "name": "xAI (Grok)",
        "icon": "❌",
        "api_type": "openai_compat",
        "base_url": "https://api.x.ai/v1",
        "prefix": "xai",
        "default_models": [],
    },
    "qwen": {
        "name": "Qwen/DashScope",
        "icon": "🌐",
        "api_type": "openai_compat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "prefix": "qwen",
        "default_models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    "opencode_free": {
        "name": "OpenCode Zen (Free)",
        "icon": "🆓",
        "api_type": "opencode_free",
        "base_url": "",
        "prefix": "opencode",
        "default_models": [],
    },
    "custom": {
        "name": "Custom Provider",
        "icon": "⚙️",
        "api_type": "openai_compat",
        "base_url": "",
        "prefix": "",
        "default_models": [],
    },
}


def get_catalog() -> dict[str, dict[str, Any]]:
    return CATALOG
```

- [ ] **Step 2: Commit**

```bash
git add src/janus/dashboard/catalog.py
git commit -m "feat: add provider catalog with 14 known providers"
```

---

### Task 9: Base.html Grouped Sidebar

**Files:**
- Modify: `src/janus/dashboard/templates/base.html`

- [ ] **Step 1: Replace the sidebar nav with grouped sections**

Replace the `<nav>` section (lines 24-53) in `base.html` with:

```html
            <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
                <div class="mb-1">
                    <p class="px-3 py-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">Monitor</p>
                    <a href="/dashboard" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block overview_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>
                        Overview
                    </a>
                    <a href="/dashboard/usage" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block usage_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                        Usage
                    </a>
                    <a href="/dashboard/analytics" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block analytics_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z"/></svg>
                        Analytics
                    </a>
                </div>
                <div class="mb-1">
                    <p class="px-3 py-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">Manage</p>
                    <a href="/dashboard/providers" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block providers_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
                        Providers
                    </a>
                    <a href="/dashboard/combos" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block combos_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                        Combos
                    </a>
                    <a href="/dashboard/savers" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block savers_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.121 14.121L19 19m-7-7l7-7m-7 7l-2.879 2.879M12 12L9.121 9.121m0 5.758a3 3 0 10-4.243-4.243 3 3 0 004.243 4.243z"/></svg>
                        Token Savers
                    </a>
                    <a href="/dashboard/budgets" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block budgets_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1"/></svg>
                        Budgets
                    </a>
                </div>
                <div class="mb-1">
                    <p class="px-3 py-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">Access</p>
                    <a href="/dashboard/keys" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block keys_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>
                        API Keys
                    </a>
                    <a href="/dashboard/tools" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block tools_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
                        Tool Setup
                    </a>
                </div>
                <div class="mb-1">
                    <p class="px-3 py-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">System</p>
                    <a href="/dashboard/pricing" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block pricing_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1"/></svg>
                        Pricing
                    </a>
                    <a href="/dashboard/settings" class="flex items-center px-3 py-2 rounded-md text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white transition-colors {% block settings_active %}{% endblock %}">
                        <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                        Settings
                    </a>
                </div>
            </nav>
```

- [ ] **Step 2: Run existing dashboard tests**

Run: `.venv/bin/python -m pytest tests/integration/test_dashboard.py -v`
Expected: All pass (nav structure changed but test assertions check for "Janus" text)

- [ ] **Step 3: Commit**

```bash
git add src/janus/dashboard/templates/base.html
git commit -m "feat: grouped sidebar nav (Monitor/Manage/Access/System)"
```

---

### Task 10: Provider CRUD Routes + Templates

**Files:**
- Modify: `src/janus/dashboard/routes.py`
- Create: `src/janus/dashboard/templates/providers.html` (rewrite)
- Create: `src/janus/dashboard/templates/providers_partial.html`
- Test: `tests/integration/test_dashboard_crud.py`

- [ ] **Step 1: Write failing tests for provider CRUD endpoints**

Create `tests/integration/test_dashboard_crud.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[],
    )
    return create_app(config=cfg)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_provider_create(client):
    r = await client.post("/dashboard/api/providers", data={
        "id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "models": "gpt-4o,gpt-4o-mini",
    })
    assert r.status_code == 200
    assert "openai" in r.text


async def test_provider_toggle(client):
    await client.post("/dashboard/api/providers", data={
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": "",
        "models": "",
    })
    r = await client.patch("/dashboard/api/providers/test/toggle")
    assert r.status_code == 200


async def test_provider_delete(client):
    await client.post("/dashboard/api/providers", data={
        "id": "todelete",
        "prefix": "todelete",
        "api_type": "openai_compat",
        "base_url": "https://delete.local",
        "api_key": "",
        "models": "",
    })
    r = await client.delete("/dashboard/api/providers/todelete")
    assert r.status_code == 200


async def test_provider_edit(client):
    await client.post("/dashboard/api/providers", data={
        "id": "edit",
        "prefix": "edit",
        "api_type": "openai_compat",
        "base_url": "https://old.local",
        "api_key": "old",
        "models": "m1",
    })
    r = await client.put("/dashboard/api/providers/edit", data={
        "prefix": "edit",
        "api_type": "openai_compat",
        "base_url": "https://new.local",
        "api_key": "new",
        "models": "m1,m2",
    })
    assert r.status_code == 200


async def test_combo_create(client):
    r = await client.post("/dashboard/api/combos", data={
        "name": "test-combo",
        "models": "openai/gpt-4o,anthropic/claude-sonnet-4-20250514",
    })
    assert r.status_code == 200
    assert "test-combo" in r.text


async def test_combo_delete(client):
    await client.post("/dashboard/api/combos", data={
        "name": "del-combo",
        "models": "a/b",
    })
    r = await client.delete("/dashboard/api/combos/1")
    assert r.status_code == 200


async def test_savers_page(client):
    r = await client.get("/dashboard/savers")
    assert r.status_code == 200
    assert "Token Savers" in r.text or "RTK" in r.text


async def test_tools_page(client):
    r = await client.get("/dashboard/tools")
    assert r.status_code == 200
    assert "Claude Code" in r.text or "ANTHROPIC_BASE_URL" in r.text


async def test_pricing_page(client):
    r = await client.get("/dashboard/pricing")
    assert r.status_code == 200


async def test_settings_page(client):
    r = await client.get("/dashboard/settings")
    assert r.status_code == 200


async def test_setting_update(client):
    r = await client.post("/dashboard/api/settings", data={
        "key": "saver_rtk_enabled",
        "value": "false",
    })
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_dashboard_crud.py -v`
Expected: FAIL (routes don't exist)

- [ ] **Step 3: Add all CRUD routes to `dashboard/routes.py`**

Add these imports at the top of `routes.py`:

```python
from janus.dashboard.catalog import get_catalog
from janus.dashboard.reload import reload_combos, reload_providers, reload_savers, reload_pricing
from janus.storage.combos_db import (
    create_combo as db_create_combo,
    delete_combo as db_delete_combo,
    list_combos as db_list_combos,
    update_combo as db_update_combo,
)
from janus.storage.pricing_db import (
    create_or_update_pricing_override,
    delete_pricing_override,
)
from janus.storage.providers_db import (
    create_provider as db_create_provider,
    delete_provider as db_delete_provider,
    list_providers as db_list_providers,
    toggle_provider as db_toggle_provider,
    update_provider as db_update_provider,
)
from janus.storage.settings import get_all_settings, set_setting
```

Add the new routes (after the existing routes, before the keys endpoints):

```python
# ---- Provider CRUD ----

@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    providers = await db_list_providers(db_path)
    catalog = get_catalog()
    context: dict[str, Any] = {
        "request": request,
        "providers": providers,
        "catalog": catalog,
    }
    return _templates.TemplateResponse(request, "providers.html", context)


@router.post("/api/providers", response_class=HTMLResponse)
async def api_create_provider(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    await db_create_provider(db_path, {
        "id": params["id"][0],
        "prefix": params["prefix"][0],
        "api_type": params["api_type"][0],
        "base_url": params["base_url"][0],
        "api_key": params.get("api_key", [""])[0] or None,
        "models": models,
    })
    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.put("/api/providers/{provider_id}", response_class=HTMLResponse)
async def api_update_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    await db_update_provider(db_path, provider_id, {
        "prefix": params["prefix"][0],
        "api_type": params["api_type"][0],
        "base_url": params["base_url"][0],
        "api_key": params.get("api_key", [""])[0] or None,
        "models": models,
    })
    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.patch("/api/providers/{provider_id}/toggle", response_class=HTMLResponse)
async def api_toggle_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await db_toggle_provider(db_path, provider_id)
    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.delete("/api/providers/{provider_id}", response_class=HTMLResponse)
async def api_delete_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await db_delete_provider(db_path, provider_id)
    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


async def _providers_partial(request: Request, db_path: Path) -> HTMLResponse:
    providers = await db_list_providers(db_path)
    context: dict[str, Any] = {
        "request": request,
        "providers": providers,
    }
    return _templates.TemplateResponse(request, "providers_partial.html", context)


# ---- Combo CRUD ----

@router.get("/combos", response_class=HTMLResponse)
async def combos_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    combos = await db_list_combos(db_path)
    context: dict[str, Any] = {
        "request": request,
        "combos": combos,
    }
    return _templates.TemplateResponse(request, "combos.html", context)


@router.post("/api/combos", response_class=HTMLResponse)
async def api_create_combo(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    await db_create_combo(db_path, {"name": params["name"][0], "models": models})
    await reload_combos(request.app)
    return await _combos_partial(request, db_path)


@router.put("/api/combos/{combo_id}", response_class=HTMLResponse)
async def api_update_combo(request: Request, combo_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    await db_update_combo(db_path, combo_id, {"name": params["name"][0], "models": models})
    await reload_combos(request.app)
    return await _combos_partial(request, db_path)


@router.delete("/api/combos/{combo_id}", response_class=HTMLResponse)
async def api_delete_combo(request: Request, combo_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await db_delete_combo(db_path, combo_id)
    await reload_combos(request.app)
    return await _combos_partial(request, db_path)


async def _combos_partial(request: Request, db_path: Path) -> HTMLResponse:
    combos = await db_list_combos(db_path)
    context: dict[str, Any] = {
        "request": request,
        "combos": combos,
    }
    return _templates.TemplateResponse(request, "combos_partial.html", context)


# ---- Token Savers ----

@router.get("/savers", response_class=HTMLResponse)
async def savers_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    settings = await get_all_settings(db_path)
    context: dict[str, Any] = {
        "request": request,
        "settings": settings,
    }
    return _templates.TemplateResponse(request, "savers.html", context)


@router.post("/api/settings", response_class=HTMLResponse)
async def api_update_setting(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    key = params["key"][0]
    value = params["value"][0]
    await set_setting(db_path, key, value)
    if key.startswith("saver_"):
        await reload_savers(request.app)
    return HTMLResponse(content="", status_code=200)


# ---- Tool Setup ----

@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    settings = await get_all_settings(db_path)
    require_key = settings.get("server_require_api_key", "false") == "true"
    base_url = f"http://localhost:{request.app.state.config.server.port}/v1"
    context: dict[str, Any] = {
        "request": request,
        "base_url": base_url,
        "require_key": require_key,
    }
    return _templates.TemplateResponse(request, "tools.html", context)


# ---- Pricing ----

@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.pricing.builtin import BUILTIN_PRICING
    from janus.storage.pricing_db import list_pricing_overrides

    overrides = await list_pricing_overrides(db_path)
    context: dict[str, Any] = {
        "request": request,
        "builtin": sorted(BUILTIN_PRICING.keys()),
        "overrides": overrides,
    }
    return _templates.TemplateResponse(request, "pricing.html", context)


@router.post("/api/pricing", response_class=HTMLResponse)
async def api_create_pricing(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    body = await request.body()
    params = parse_qs(body.decode())
    await create_or_update_pricing_override(db_path, {
        "model": params["model"][0],
        "input_per_mtok": float(params["input_per_mtok"][0]),
        "output_per_mtok": float(params["output_per_mtok"][0]),
        "cache_creation_per_mtok": float(params.get("cache_creation_per_mtok", ["0"])[0]),
        "cache_read_per_mtok": float(params.get("cache_read_per_mtok", ["0"])[0]),
    })
    await reload_pricing(request.app)
    return HTMLResponse(content="", status_code=200)


@router.delete("/api/pricing/{model}", response_class=HTMLResponse)
async def api_delete_pricing(request: Request, model: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await delete_pricing_override(db_path, model)
    await reload_pricing(request.app)
    return HTMLResponse(content="", status_code=200)


# ---- Settings ----

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    settings = await get_all_settings(db_path)
    context: dict[str, Any] = {
        "request": request,
        "settings": settings,
        "config": request.app.state.config,
    }
    return _templates.TemplateResponse(request, "settings.html", context)
```

- [ ] **Step 4: Create all templates**

Create `providers.html` — rewrite the existing file with a rich card grid. Include an "Add Provider" button that opens a modal with the catalog gallery. Each provider card shows status, models, edit/toggle/delete buttons.

Create `providers_partial.html` — just the provider card grid for HTMX swaps.

Create `combos.html` — rewrite with combo cards, add/edit/delete buttons, Sortable.js for drag-drop.

Create `combos_partial.html` — just the combo list for HTMX swaps.

Create `savers.html` — three toggle cards (RTK, Caveman, Ponytail) with descriptions and HTMX toggle switches. Ponytail has a level selector.

Create `tools.html` — four cards (Claude Code, Codex, Cursor, Cline) with copy-paste env vars.

Create `pricing.html` — table of builtin models + override rows with add/edit/delete.

Create `settings.html` — server settings form (require_api_key toggle, host/port read-only) + export config button.

All templates extend `base.html` and use `{% block content %}`. Follow the dark theme from `base.html` (bg-gray-900, text-gray-300, etc.). Use HTMX attributes for all form submissions (`hx-post`, `hx-delete`, `hx-patch`, `hx-put`, `hx-target`, `hx-swap`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_dashboard_crud.py -v`
Expected: All tests pass

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/janus/dashboard/ tests/integration/test_dashboard_crud.py
git commit -m "feat: full dashboard CRUD — providers, combos, savers, tools, pricing, settings"
```

---

### Task 11: AGENTS.md Update

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update AGENTS.md**

Add a section covering:
- Dashboard is now full CRUD — providers, combos, savers, pricing, settings managed from DB (not YAML)
- YAML config is a seed file (loaded on first startup, then DB is source of truth)
- `seed_from_config()` in `database.py` handles one-time YAML → DB migration
- Hot-reload helpers in `dashboard/reload.py` (`reload_providers`, `reload_combos`, `reload_savers`, `reload_pricing`)
- Dashboard `_ensure_db` now also triggers reload functions (for ASGITransport test compat)
- New DB tables: `providers`, `combos`, `settings`, `pricing_overrides`
- Provider catalog in `dashboard/catalog.py`

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md for Phase 9 (dashboard CRUD)"
```

---

### Task 12: Verification + PR

- [ ] **Step 1: Run full verification**

```bash
.venv/bin/ruff check src/janus/ tests/
.venv/bin/ruff format --check src/janus/ tests/
.venv/bin/mypy src/janus/
.venv/bin/python -m pytest -x -q
```

Expected: All clean, all tests pass.

- [ ] **Step 2: Create branch and PR**

```bash
git checkout -b phase-9/dashboard-crud
git push -u origin phase-9/dashboard-crud
gh pr create --title "Phase 9: Full Dashboard CRUD" --body "..." --base main
```

- [ ] **Step 3: After squash-merge, pull and prune**

```bash
git checkout main
git pull --rebase origin main
git branch -d phase-9/dashboard-crud
git push origin --delete phase-9/dashboard-crud
```
