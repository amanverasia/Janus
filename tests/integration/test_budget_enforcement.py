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
from janus.storage.settings import set_setting


def _ts(days_ago: int) -> str:
    value = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    return value.replace(tzinfo=None).isoformat(sep=" ")


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


@pytest.mark.asyncio
async def test_budget_block_uses_configured_reporting_timezone(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    registry = ProviderRegistry()
    config = JanusConfig(
        server=ServerSettings(require_api_key=True, data_dir=tmp_path),
    )
    app = create_app(registry=registry, config=config)
    app.state.db_path = db_path

    raw_key, record = await create_key(db_path, name="test")
    await set_setting(db_path, "server_reporting_timezone", "Asia/Kolkata")
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
    assert "midnight (Asia/Kolkata)" in resp.json()["error"]["message"]
    assert 1 <= int(resp.headers["Retry-After"]) <= 25 * 60 * 60


@pytest.mark.parametrize("daily_limit", [None, 100.0])
@pytest.mark.parametrize("stream", [False, True])
async def test_absolute_budget_blocks_historical_spend_without_retry_after(
    tmp_path, daily_limit, stream
):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    raw_key, key = await create_key(db_path, name="absolute-key")
    other_key, _other = await create_key(db_path, name="other-key")
    await create_or_update_budget(
        db_path, key_id=key["id"], daily_limit=daily_limit, absolute_limit=10
    )
    await _seed_cost(db_path, 10, key["id"])
    async with get_connection(db_path) as db:
        await db.execute("UPDATE usage SET timestamp = '2025-01-01 00:00:00'")
        await db.commit()
    app = create_app(config=JanusConfig(server=ServerSettings(data_dir=tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "nonexistent",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": stream,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        unaffected = await client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {other_key}"},
        )
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "budget_exceeded"
    assert error["budget_period"] == "absolute"
    assert error["absolute_limit"] == error["total_spend"] == 10
    assert error["resets_at"] is None
    assert "does not reset" in error["message"]
    assert "midnight" not in error["message"]
    assert "retry-after" not in response.headers
    assert unaffected.status_code != 429


async def test_absolute_budget_takes_precedence_over_global_daily_reset(tmp_path):
    from janus.api.routes import _check_budgets

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    _, key = await create_key(db_path, name="limited-key")
    await create_or_update_budget(db_path, key_id=key["id"], daily_limit=1, absolute_limit=2)
    await create_or_update_budget(db_path, key_id=None, daily_limit=1)
    await _seed_cost(db_path, 2, key["id"])
    blocked = await _check_budgets(db_path, key["id"])
    assert blocked.status_code == 429
    assert "retry-after" not in blocked.headers
    await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=10)
    daily_block = await _check_budgets(db_path, key["id"])
    assert daily_block.status_code == 429
    assert "retry-after" in daily_block.headers
    await create_or_update_budget(db_path, key_id=key["id"], daily_limit=None)
    assert (await _check_budgets(db_path, key["id"])).status_code == 429
    await create_or_update_budget(db_path, key_id=None, daily_limit=10)
    assert await _check_budgets(db_path, key["id"]) is None


async def test_absolute_budget_can_be_increased_or_removed_to_resume(tmp_path):
    from janus.api.routes import _check_budgets

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    _, key = await create_key(db_path, name="limited-key")
    await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=2)
    await _seed_cost(db_path, 2, key["id"])
    assert (await _check_budgets(db_path, key["id"])).status_code == 429
    await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=3)
    assert await _check_budgets(db_path, key["id"]) is None
    await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=2)
    await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=None)
    assert await _check_budgets(db_path, key["id"]) is None
