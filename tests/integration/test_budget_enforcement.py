import datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.providers.registry import ProviderRegistry
from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget
from janus.storage.database import get_connection, init_db


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


async def _seed_cost(db_path: str | Path, cost: float, key_id: int | None = None) -> None:
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
