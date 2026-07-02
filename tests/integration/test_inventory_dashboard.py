import socket

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


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
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_inventory_overview_page(client):
    r = await client.get("/dashboard/inventory")
    assert r.status_code == 200
    assert "Key Inventory" in r.text


async def test_inventory_keys_page(client):
    r = await client.get("/dashboard/inventory/keys")
    assert r.status_code == 200
    assert "Upstream Keys" in r.text


async def test_inventory_add_page(client):
    r = await client.get("/dashboard/inventory/add")
    assert r.status_code == 200
    assert "Add Upstream Keys" in r.text


@pytest.mark.asyncio
@respx.mock
async def test_inventory_submit_key(client):
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )

    r = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": "sk-proj-test-key", "provider_id": "openai"},
    )
    assert r.status_code == 200
    assert "pending_validation" in r.text

    export = await client.get("/dashboard/api/inventory/export")
    assert export.status_code == 200
    payload = export.json()
    assert payload["count"] == 1


async def test_inventory_keys_partial_polls_when_pending(client):
    create = await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "gsk_testkey", "provider_id": "groq"},
    )
    assert create.status_code == 200
    assert "Validation in progress" in create.text
    assert 'hx-trigger="every 2s"' in create.text

    partial = await client.get("/dashboard/api/inventory/keys/partial")
    assert partial.status_code == 200


async def test_inventory_submit_status_endpoint(client):
    create = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": "sk-proj-status-test", "provider_id": "openai"},
    )
    assert create.status_code == 200
    assert 'hx-trigger="every 2s"' in create.text

    export = await client.get("/dashboard/api/inventory/export")
    key_id = export.json()["keys"][0]["id"]

    status = await client.get(f"/dashboard/api/inventory/submit/status?ids={key_id}")
    assert status.status_code == 200
    assert "pending_validation" in status.text


async def test_inventory_delete_key(client):
    create = await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "gsk_testkey", "provider_id": "groq"},
    )
    assert create.status_code == 200

    keys_page = await client.get("/dashboard/inventory/keys")
    assert keys_page.status_code == 200

    export_before = await client.get("/dashboard/api/inventory/export")
    key_id = export_before.json()["keys"][0]["id"]

    delete = await client.delete(f"/dashboard/api/inventory/keys/{key_id}")
    assert delete.status_code == 200

    export_after = await client.get("/dashboard/api/inventory/export")
    assert export_after.json()["count"] == 0
