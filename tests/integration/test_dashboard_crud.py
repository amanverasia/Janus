import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_provider_create(client):
    r = await client.post(
        "/dashboard/api/providers",
        data={
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "models": "gpt-4o,gpt-4o-mini",
        },
    )
    assert r.status_code == 200


async def test_provider_toggle(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://test.local",
            "api_key": "",
            "models": "",
        },
    )
    r = await client.patch("/dashboard/api/providers/test/toggle")
    assert r.status_code == 200


async def test_provider_delete(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "todelete",
            "prefix": "todelete",
            "api_type": "openai_compat",
            "base_url": "https://delete.local",
            "api_key": "",
            "models": "",
        },
    )
    r = await client.delete("/dashboard/api/providers/todelete")
    assert r.status_code == 200


async def test_provider_edit(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "edit",
            "prefix": "edit",
            "api_type": "openai_compat",
            "base_url": "https://old.local",
            "api_key": "old",
            "models": "m1",
        },
    )
    r = await client.put(
        "/dashboard/api/providers/edit",
        data={
            "prefix": "edit",
            "api_type": "openai_compat",
            "base_url": "https://new.local",
            "api_key": "new",
            "models": "m1,m2",
        },
    )
    assert r.status_code == 200


async def test_combo_create(client):
    r = await client.post(
        "/dashboard/api/combos",
        data={
            "name": "test-combo",
            "models": "openai/gpt-4o,anthropic/claude-sonnet-4-20250514",
        },
    )
    assert r.status_code == 200


async def test_combo_delete(client):
    await client.post(
        "/dashboard/api/combos",
        data={
            "name": "del-combo",
            "models": "a/b",
        },
    )
    r = await client.delete("/dashboard/api/combos/1")
    assert r.status_code == 200


async def test_savers_page(client):
    r = await client.get("/dashboard/savers")
    assert r.status_code == 200


async def test_tools_page(client):
    r = await client.get("/dashboard/tools")
    assert r.status_code == 200


async def test_pricing_page(client):
    r = await client.get("/dashboard/pricing")
    assert r.status_code == 200


async def test_settings_page(client):
    r = await client.get("/dashboard/settings")
    assert r.status_code == 200


async def test_setting_update(client):
    r = await client.post(
        "/dashboard/api/settings",
        data={
            "key": "saver_rtk_enabled",
            "value": "false",
        },
    )
    assert r.status_code == 200


@respx.mock
async def test_provider_test_connection(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "models": "gpt-4o",
        },
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "r", "choices": []})
    )
    r = await client.post("/dashboard/api/providers/openai/test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == 200
    assert data["latency_ms"] >= 0


@respx.mock
async def test_provider_test_connection_failure(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "bad",
            "prefix": "bad",
            "api_type": "openai_compat",
            "base_url": "https://bad.local/v1",
            "api_key": "sk-test",
            "models": "m1",
        },
    )
    respx.post("https://bad.local/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "invalid key"})
    )
    r = await client.post("/dashboard/api/providers/bad/test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["status"] == 401


async def test_provider_test_connection_not_found(client):
    r = await client.post("/dashboard/api/providers/nonexistent/test")
    assert r.status_code == 404


async def test_export_yaml(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "models": "gpt-4o",
        },
    )
    r = await client.get("/dashboard/api/export")
    assert r.status_code == 200
    assert "text/yaml" in r.headers["content-type"]
    assert "janus-config.yaml" in r.headers["content-disposition"]
    assert "openai" in r.text
    assert "gpt-4o" in r.text


async def test_reset_to_defaults(client):
    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "temp",
            "prefix": "temp",
            "api_type": "openai_compat",
            "base_url": "https://temp.local/v1",
            "api_key": "",
            "models": "m1",
        },
    )
    r = await client.post("/dashboard/api/reset")
    assert r.status_code == 200
