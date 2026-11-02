import time

import pytest
import respx
from httpx import Response

from janus.inventory.key_checker import check_upstream_key
from janus.storage.database import init_db
from janus.storage.routing_health import load_routing_health, mark_routing_unhealthy
from janus.storage.upstream_keys import (
    create_upstream_key,
    get_upstream_key,
    list_upstream_key_history,
)
from tests.fixtures.url_mock import mocked_route


@pytest.mark.asyncio
@respx.mock
async def test_successful_recheck_clears_routing_unhealthy(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-test")
    await mark_routing_unhealthy(
        db_path, record["id"], reason="payment_error", status_code=402, at=time.time()
    )
    assert record["id"] in await load_routing_health(db_path)

    mocked_route("GET", "https://api.openai.com/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    mocked_route("POST", "https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )
    await check_upstream_key(db_path, record["id"])

    assert await load_routing_health(db_path) == {}
    updated = await get_upstream_key(db_path, record["id"])
    assert updated is not None
    assert updated["routing_health_status"] is None
    assert updated["routing_health_reason"] is None
    history = await list_upstream_key_history(db_path, record["id"])
    assert ("routing_unhealthy", "active") in {
        (h["previous_status"], h["new_status"]) for h in history
    }


@pytest.mark.asyncio
@respx.mock
async def test_failed_recheck_keeps_routing_unhealthy(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-test")
    await mark_routing_unhealthy(
        db_path, record["id"], reason="auth_error", status_code=401, at=time.time()
    )
    mocked_route("GET", "https://api.openai.com/v1/models").mock(return_value=Response(401))
    await check_upstream_key(db_path, record["id"])
    assert record["id"] in await load_routing_health(db_path)
