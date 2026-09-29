import socket

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key
from tests.fixtures.dashboard_auth import with_dashboard_auth
from tests.fixtures.url_mock import mocked_route

_ZAI_QUOTA_PATH = "/api/monitor/usage/quota/limit"


@pytest.fixture(autouse=True)
def mock_public_dns(monkeypatch):
    def fake_getaddrinfo(
        host: str,
        port: object,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        del host, port, family, type, proto, flags
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setattr(
        "janus.dashboard.inventory_routes._schedule_recheck",
        lambda key_id, db_path: None,
    )
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return with_dashboard_auth(create_app(config=cfg))


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_key(client: AsyncClient, provider_id: str, key_value: str) -> str:
    await client.get("/dashboard/api/v2/state/inventory")
    db_path = str(client._transport.app.state.db_path)
    created = await create_upstream_key(db_path, provider_id=provider_id, key_value=key_value)
    return str(created["id"])


@respx.mock
async def test_refresh_account_value_endpoint(client, app):
    mocked_route("GET", f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "limits": [{"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 42}]
                },
            },
        )
    )
    key_id = await _create_key(client, "zhipu", "a" * 32 + "." + "b" * 16)

    response = await client.post(f"/dashboard/api/inventory/keys/{key_id}/account-value/refresh")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    state = payload["account_value"]
    assert state["status"] == "ok"
    assert state["value"]["windows"][0]["label"] == "5h"
    assert state["value"]["windows"][0]["used_percent"] == 42.0

    keys_page = await client.get(
        "/dashboard/api/v2/state/inventory-keys", params={"provider_id": "zhipu"}
    )
    row = next(item for item in keys_page.json()["data"]["keys"] if item["id"] == key_id)
    assert row["account_value"]["windows"][0]["used_percent"] == 42.0
    assert row["account_value_status"] == "ok"
    assert row["account_value_checked_at"] is not None


async def test_refresh_account_value_unknown_key(client):
    response = await client.post("/dashboard/api/inventory/keys/nope/account-value/refresh")
    assert response.status_code == 404


@respx.mock
async def test_inventory_overview_surfaces_worst_usage(client, app):
    mocked_route("GET", f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "limits": [{"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 95}]
                },
            },
        )
    )
    key_id = await _create_key(client, "zhipu", "a" * 32 + "." + "b" * 16)
    assert client._transport.app is not None
    await update_upstream_key(
        str(client._transport.app.state.db_path),
        key_id,
        {"status": "active", "is_valid": 1},
    )
    await client.post(f"/dashboard/api/inventory/keys/{key_id}/account-value/refresh")

    overview = await client.get("/dashboard/api/v2/state/inventory")
    assert overview.status_code == 200
    credit_rows = overview.json()["data"]["credit_summary"]
    zhipu = next(row for row in credit_rows if row["id"] == "zhipu")
    assert zhipu["worst_usage_percent"] == 95.0


@respx.mock
async def test_quota_exhausted_alert_fires(client, app):
    mocked_route("GET", f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "limits": [{"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 97}]
                },
            },
        )
    )
    key_id = await _create_key(client, "zhipu", "a" * 32 + "." + "b" * 16)
    assert client._transport.app is not None
    await update_upstream_key(
        str(client._transport.app.state.db_path),
        key_id,
        {"status": "active", "is_valid": 1},
    )
    await client.post(f"/dashboard/api/inventory/keys/{key_id}/account-value/refresh")

    state = await client.get("/dashboard/api/v2/state/settings")
    assert state.status_code == 200
    alerts = state.json()["alerts"]
    exhausted = [alert for alert in alerts if alert["id"] == "inventory:quota_exhausted"]
    assert exhausted and "1 inventory account" in exhausted[0]["detail"]


@respx.mock
async def test_key_check_runs_account_value_probe(client, app):
    mocked_route("GET", "https://openrouter.ai/api/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "openai/gpt-4o"}]})
    )
    mocked_route("GET", "https://openrouter.ai/api/v1/key").mock(
        return_value=Response(
            200, json={"data": {"limit": 50.0, "limit_remaining": 25.0, "usage": 25.0}}
        )
    )
    mocked_route("POST", "https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )
    key_id = await _create_key(client, "openrouter", "sk-or-v1-" + "c" * 40)

    test = await client.post(f"/dashboard/api/inventory/keys/{key_id}/test")
    assert test.status_code == 200

    state = await client.get(
        "/dashboard/api/v2/state/inventory-keys", params={"provider_id": "openrouter"}
    )
    row = next(item for item in state.json()["data"]["keys"] if item["id"] == key_id)
    assert row["account_value_status"] == "ok"
    assert row["account_value"]["credits"]["remaining"] == 25.0
    assert row["credits_remaining"] == 25.0
    assert row["credits_total"] == 50.0


@respx.mock
async def test_key_check_survives_probe_failure(client, app):
    mocked_route("GET", "https://openrouter.ai/api/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "openai/gpt-4o"}]})
    )
    mocked_route("GET", "https://openrouter.ai/api/v1/key").mock(return_value=Response(503))
    mocked_route("POST", "https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )
    key_id = await _create_key(client, "openrouter", "sk-or-v1-" + "d" * 40)

    test = await client.post(f"/dashboard/api/inventory/keys/{key_id}/test")
    assert test.status_code == 200
    assert test.json()["ok"] is True

    state = await client.get(
        "/dashboard/api/v2/state/inventory-keys", params={"provider_id": "openrouter"}
    )
    row = next(item for item in state.json()["data"]["keys"] if item["id"] == key_id)
    assert row["account_value_status"] == "unavailable"
    assert row["status"] == "active"
