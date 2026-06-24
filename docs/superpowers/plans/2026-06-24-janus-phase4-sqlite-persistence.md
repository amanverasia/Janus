# Janus Phase 4: SQLite Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add SQLite database for API-key management and usage tracking. YAML stays for providers/combos; DB stores runtime state.

**Architecture:** New `storage/` package using `aiosqlite`. DB at `~/.janus/janus.db`. API keys stored SHA256-hashed. Usage recorded async after each request.

**Tech Stack:** Python 3.11+, aiosqlite, FastAPI, typer.

---

### Task 1: Database layer

**Files:** `src/janus/storage/__init__.py`, `src/janus/storage/database.py`, `tests/unit/storage/__init__.py`, `tests/unit/storage/test_database.py`

- [ ] **Step 1: Add aiosqlite dependency**

Add `"aiosqlite>=0.20"` to `pyproject.toml` dependencies. Run `pip install -e ".[dev]"`.

- [ ] **Step 2: Write failing test**

```python
# tests/unit/storage/test_database.py
import tempfile
import pytest
from janus.storage.database import init_db, get_connection


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = [row[0] for row in await cur.fetchall()]
    assert "api_keys" in tables
    assert "usage" in tables


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await init_db(db_path)  # should not error
```

- [ ] **Step 3: Implement**

```python
# src/janus/storage/database.py
from __future__ import annotations
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

CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(timestamp);
"""


async def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_connection(db_path: str | Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    return db
```

- [ ] **Step 4: Run tests, commit**

```bash
.venv/bin/python -m pytest tests/unit/storage/test_database.py -v
git add -A && git commit -m "feat: SQLite database layer with schema init"
```

---

### Task 2: API-key management

**Files:** `src/janus/storage/api_keys.py`, `tests/unit/storage/test_api_keys.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/storage/test_api_keys.py
import pytest
from janus.storage.database import init_db
from janus.storage.api_keys import create_key, list_keys, revoke_key, verify_key


@pytest.mark.asyncio
async def test_create_key_returns_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test-key")
    assert key.startswith("sk-janus-")
    assert len(key) == len("sk-janus-") + 32
    assert record["name"] == "test-key"
    assert record["prefix"] == key[:16]  # first 16 chars stored for display


@pytest.mark.asyncio
async def test_verify_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, name="test")
    assert await verify_key(db_path, key) is True
    assert await verify_key(db_path, "sk-janus-wrong") is False
    assert await verify_key(db_path, "not-even-a-key") is False


@pytest.mark.asyncio
async def test_list_keys(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_key(db_path, name="key1")
    await create_key(db_path, name="key2")
    keys = await list_keys(db_path)
    assert len(keys) == 2
    assert "key_hash" not in str(keys[0]) or len(keys[0]["prefix"]) <= 16


@pytest.mark.asyncio
async def test_revoke_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test")
    await revoke_key(db_path, record["id"])
    assert await verify_key(db_path, key) is False


@pytest.mark.asyncio
async def test_create_key_hash_not_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, name="test")
    keys = await list_keys(db_path)
    # The full key should NOT appear in the stored data
    assert key not in str(keys)
```

- [ ] **Step 2: Implement**

```python
# src/janus/storage/api_keys.py
from __future__ import annotations
import hashlib
import secrets
from pathlib import Path
from typing import Any
import aiosqlite
from .database import get_connection


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def create_key(db_path: str | Path, name: str) -> tuple[str, dict[str, Any]]:
    raw = secrets.token_hex(16)
    key = f"sk-janus-{raw}"
    key_hash = _hash_key(key)
    prefix = key[:16]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO api_keys (name, key_hash, prefix) VALUES (?, ?, ?)",
            (name, key_hash, prefix),
        )
        await db.commit()
        record_id = cursor.lastrowid
    return key, {"id": record_id, "name": name, "prefix": prefix}


async def verify_key(db_path: str | Path, key: str) -> bool:
    if not key.startswith("sk-janus-"):
        return False
    key_hash = _hash_key(key)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def list_keys(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, name, prefix, is_active, created_at FROM api_keys ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def revoke_key(db_path: str | Path, key_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,)
        )
        await db.commit()
```

- [ ] **Step 3: Run tests, commit**

---

### Task 3: Usage recording

**Files:** `src/janus/storage/usage.py`, `tests/unit/storage/test_usage.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/storage/test_usage.py
import pytest
from janus.storage.database import init_db
from janus.storage.usage import record_usage, get_usage_stats


@pytest.mark.asyncio
async def test_record_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(db_path, provider_id="glm", model="glm-4.7", input_tokens=100, output_tokens=50, status=200)
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50


@pytest.mark.asyncio
async def test_record_multiple_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(db_path, provider_id="glm", model="glm-4.7", input_tokens=100, output_tokens=50, status=200)
    await record_usage(db_path, provider_id="an", model="claude", input_tokens=200, output_tokens=100, status=200)
    await record_usage(db_path, provider_id="glm", model="glm-4.7", input_tokens=50, output_tokens=25, status=429)
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 3
    assert stats["total_input_tokens"] == 350
    assert stats["total_output_tokens"] == 175


@pytest.mark.asyncio
async def test_usage_stats_by_model(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(db_path, provider_id="glm", model="glm-4.7", input_tokens=100, output_tokens=50, status=200)
    await record_usage(db_path, provider_id="glm", model="glm-4.7", input_tokens=200, output_tokens=100, status=200)
    await record_usage(db_path, provider_id="an", model="claude", input_tokens=50, output_tokens=25, status=200)
    stats = await get_usage_stats(db_path)
    by_model = {m["model"]: m for m in stats["by_model"]}
    assert by_model["glm-4.7"]["requests"] == 2
    assert by_model["glm-4.7"]["input_tokens"] == 300
    assert by_model["claude"]["requests"] == 1
```

- [ ] **Step 2: Implement**

```python
# src/janus/storage/usage.py
from __future__ import annotations
from pathlib import Path
from typing import Any
import logging
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
    status: int = 0,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage (provider_id, model, account_id, input_tokens, output_tokens, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider_id, model, account_id, input_tokens, output_tokens, status),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)


async def get_usage_stats(db_path: str | Path) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(input_tokens),0) as inp, COALESCE(SUM(output_tokens),0) as outp FROM usage"
        ) as cur:
            row = await cur.fetchone()
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

- [ ] **Step 3: Run tests, commit**

---

### Task 4: App + deps + routes integration

**Files:** `src/janus/app.py`, `src/janus/api/deps.py`, `src/janus/api/routes.py`, `tests/integration/test_api.py`

- [ ] **Step 1: Update app.py**

Store db_path on app state, init DB on startup:

```python
# In create_app(), after config setup:
from janus.storage.database import init_db
import asyncio

db_path = config.server.data_dir / "janus.db"
asyncio.get_event_loop().run_until_complete(init_db(db_path)) if not asyncio.get_event_loop().is_running() else None
# Better: use a startup event or just run sync init
app.state.db_path = db_path
```

Actually, for simplicity, use FastAPI lifespan:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = app.state.db_path
    await init_db(db_path)
    yield

# In create_app:
app = FastAPI(title="Janus", version="0.1.0", lifespan=lifespan)
app.state.db_path = config.server.data_dir / "janus.db"
```

- [ ] **Step 2: Update deps.py — check DB keys**

```python
async def require_api_key(request: Request, authorization: str = Header(default="")) -> None:
    config = request.app.state.config
    if not config.server.require_api_key:
        return
    # Check static config keys first
    if authorization.startswith("Bearer "):
        key = authorization[7:]
        if key in config.api_keys:
            return
        # Check DB keys
        from janus.storage.api_keys import verify_key
        db_path = request.app.state.db_path
        if await verify_key(db_path, key):
            return
    raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 3: Update routes.py — record usage**

After a successful response (both stream and non-stream), record usage. For non-streaming, the usage data is in `canonical_resp.usage`. For streaming, record after stream completes (or skip — usage in streams is less reliable). Keep it simple: record for non-streaming only.

In `_handle()`, after `client_payload = client_adapter.emit_response(canonical_resp)`:

```python
# Record usage (fire-and-forget)
from janus.storage.usage import record_usage
db_path = request.app.state.db_path
await record_usage(
    db_path,
    provider_id=target.provider_config.id,
    model=target.model,
    account_id=target.account_id,
    input_tokens=canonical_resp.usage.input_tokens,
    output_tokens=canonical_resp.usage.output_tokens,
    status=result.status_code,
)
```

- [ ] **Step 4: Write integration test**

```python
@pytest.mark.asyncio
@respx.mock
async def test_usage_recorded_after_request():
    from janus.providers.registry import ProviderRegistry
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="t", prefix="t", api_type="openai_compat",
                                base_url="https://fake.local/v1", api_key="k", models=["m"]))
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    app = create_app(reg, cfg)

    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "r", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json={"model": "t/m", "messages": [{"role":"user","content":"hi"}]})

    from janus.storage.usage import get_usage_stats
    stats = await get_usage_stats(app.state.db_path)
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 10
```

- [ ] **Step 5: Run all tests, commit**

---

### Task 5: CLI — keys and usage commands

**Files:** `src/janus/cli.py`, `tests/unit/test_cli.py`

- [ ] **Step 1: Add keys and usage command groups**

```python
# In cli.py, add:

keys_app = typer.Typer(help="Manage API keys")
usage_app = typer.Typer(help="Usage statistics")
app.add_typer(keys_app, name="keys")
app.add_typer(usage_app, name="usage")


@keys_app.command("create")
def keys_create(
    name: str = typer.Option("default", "--name", "-n", help="Name for this key"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
):
    import asyncio
    from janus.config.loader import load_config
    from janus.storage.database import init_db
    from janus.storage.api_keys import create_key

    cfg = load_config(Path(config).expanduser())
    db_path = cfg.server.data_dir / "janus.db"
    asyncio.run(init_db(db_path))
    key, record = asyncio.run(create_key(db_path, name=name))
    typer.echo(f"API Key (save this — shown once): {key}")
    typer.echo(f"ID: {record['id']}  Name: {record['name']}")


@keys_app.command("list")
def keys_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
):
    import asyncio
    from janus.config.loader import load_config
    from janus.storage.database import init_db
    from janus.storage.api_keys import list_keys

    cfg = load_config(Path(config).expanduser())
    db_path = cfg.server.data_dir / "janus.db"
    asyncio.run(init_db(db_path))
    keys = asyncio.run(list_keys(db_path))
    if not keys:
        typer.echo("No API keys found.")
        return
    for k in keys:
        status = "active" if k["is_active"] else "revoked"
        typer.echo(f"  {k['id']:>3}  {k['prefix']}…  {k['name']:<20}  {status}  {k['created_at']}")


@keys_app.command("revoke")
def keys_revoke(
    key_id: int = typer.Argument(..., help="Key ID to revoke"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
):
    import asyncio
    from janus.config.loader import load_config
    from janus.storage.database import init_db
    from janus.storage.api_keys import revoke_key

    cfg = load_config(Path(config).expanduser())
    db_path = cfg.server.data_dir / "janus.db"
    asyncio.run(init_db(db_path))
    asyncio.run(revoke_key(db_path, key_id))
    typer.echo(f"Revoked key {key_id}")


@usage_app.command("stats")
def usage_stats(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
):
    import asyncio
    from janus.config.loader import load_config
    from janus.storage.database import init_db
    from janus.storage.usage import get_usage_stats

    cfg = load_config(Path(config).expanduser())
    db_path = cfg.server.data_dir / "janus.db"
    asyncio.run(init_db(db_path))
    stats = asyncio.run(get_usage_stats(db_path))
    typer.echo(f"Total requests: {stats['total_requests']}")
    typer.echo(f"Total input tokens: {stats['total_input_tokens']}")
    typer.echo(f"Total output tokens: {stats['total_output_tokens']}")
    if stats["by_model"]:
        typer.echo("\nBy model:")
        for m in stats["by_model"]:
            typer.echo(f"  {m['model']:<30}  {m['requests']:>5} requests  {m['input_tokens']:>8} in  {m['output_tokens']:>8} out")
```

- [ ] **Step 2: Write CLI tests**

```python
def test_keys_create_and_list(tmp_path):
    import tempfile, os, yaml
    config_path = os.path.join(str(tmp_path), "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    result = runner.invoke(app, ["keys", "create", "--name", "test", "--config", config_path])
    assert result.exit_code == 0
    assert "sk-janus-" in result.output
    result2 = runner.invoke(app, ["keys", "list", "--config", config_path])
    assert result2.exit_code == 0
    assert "test" in result2.output


def test_usage_stats_empty(tmp_path):
    import os, yaml
    config_path = os.path.join(str(tmp_path), "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    result = runner.invoke(app, ["usage", "stats", "--config", config_path])
    assert result.exit_code == 0
    assert "Total requests: 0" in result.output
```

- [ ] **Step 3: Run all tests, commit**

---

### Task 6: Full verification + push

- [ ] **Step 1: Run all tests + lint**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 2: Push and create PR**

```bash
git checkout -b phase4-sqlite-persistence
git push origin phase4-sqlite-persistence
gh pr create --title "feat: Phase 4 — SQLite Persistence" --body "..."
```
