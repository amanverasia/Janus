import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setattr(
        "janus.inventory.provider_key_sync.schedule_upstream_recheck",
        lambda *a, **k: None,
    )
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _list_inventory_keys(client, search: str = ""):
    params = {"search": search} if search else {}
    r = await client.get("/dashboard/api/inventory/keys/partial", params=params)
    assert r.status_code == 200
    return r.text


async def test_custom_provider_key_appears_in_key_inventory(client, app):
    r = await client.post(
        "/dashboard/api/providers",
        data={
            "id": "mycustom",
            "prefix": "mycustom",
            "api_type": "openai_compat",
            "base_url": "https://api.mycustom.example.com/v1",
            "api_key": "sk-custom-visible-key-1234567890",
            "models": "gpt-4o",
        },
    )
    assert r.status_code == 200

    html = await _list_inventory_keys(client)
    assert "mycustom" in html

    found = await _list_inventory_keys(client, search="mycustom")
    assert "mycustom" in found


async def test_update_provider_key_updates_mirrored_key(client, app):
    from janus.storage.upstream_keys import list_upstream_keys

    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "rotateprov",
            "prefix": "rotateprov",
            "api_type": "openai_compat",
            "base_url": "https://api.rotate.example.com/v1",
            "api_key": "sk-original-key-aaaaaaaaaa",
            "models": "",
        },
    )
    keys = await list_upstream_keys(app.state.db_path)
    assert len(keys) == 1
    original_id = keys[0]["id"]

    await client.put(
        "/dashboard/api/providers/rotateprov",
        data={
            "prefix": "rotateprov",
            "api_type": "openai_compat",
            "base_url": "https://api.rotate.example.com/v1",
            "api_key": "sk-rotated-key-bbbbbbbbb",
            "models": "",
        },
    )
    keys = await list_upstream_keys(app.state.db_path)
    assert len(keys) == 1
    assert keys[0]["id"] == original_id
    assert keys[0]["status"] == "pending_validation"


async def test_delete_provider_revokes_mirrored_key(client, app):
    from janus.storage.upstream_keys import find_upstream_key_by_value, list_upstream_keys

    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "delprov",
            "prefix": "delprov",
            "api_type": "openai_compat",
            "base_url": "https://api.del.example.com/v1",
            "api_key": "sk-delete-me-key-9999999999",
            "models": "",
        },
    )
    assert len(await list_upstream_keys(app.state.db_path)) == 1

    await client.delete("/dashboard/api/providers/delprov")
    assert len(await list_upstream_keys(app.state.db_path)) == 0

    found = await find_upstream_key_by_value(
        db_path=app.state.db_path, key_value="sk-delete-me-key-9999999999"
    )
    assert found is None or found["status"] == "revoked"


async def test_known_catalog_provider_key_mirrors_to_correct_inventory_id(client, app):
    from janus.storage.upstream_keys import list_upstream_keys

    await client.post(
        "/dashboard/api/providers",
        data={
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai-catalog-key-12",
            "models": "gpt-4o",
        },
    )
    keys = await list_upstream_keys(app.state.db_path)
    assert len(keys) == 1
    assert keys[0]["provider_id"] == "openai"
