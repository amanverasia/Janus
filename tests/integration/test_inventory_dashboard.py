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
    assert "Preview &amp; Add Keys" in r.text or "Preview & Add Keys" in r.text


async def test_inventory_preview_openrouter(client):
    key = "sk-or-v1-" + "a" * 20
    r = await client.post(
        "/dashboard/api/inventory/preview",
        data={"keys_text": key, "provider_id": "auto"},
    )
    assert r.status_code == 200
    assert "Confirm" in r.text
    assert "OpenRouter" in r.text


async def test_inventory_submit_provisions_routing_provider(client, tmp_path):
    from janus.storage.providers_db import get_provider

    key = "sk-or-v1-" + "b" * 20
    r = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": key, "provider_id": "auto", "provision_routing": "true"},
    )
    assert r.status_code == 200
    assert "Created" in r.text or "Using existing" in r.text
    db_path = tmp_path / "janus.db"
    row = await get_provider(db_path, "openrouter")
    assert row is not None
    assert row["prefix"] == "openrouter"


async def test_inventory_import_page(client):
    r = await client.get("/dashboard/inventory/import")
    assert r.status_code == 200
    assert "Expected JSON format" in r.text


async def test_routing_page(client):
    r = await client.get("/dashboard/routing")
    assert r.status_code == 200
    assert "Routing" in r.text
    assert "Combo fallback chains" in r.text


async def test_inventory_overview_encryption_panel(client):
    r = await client.get("/dashboard/inventory")
    assert r.status_code == 200
    assert "Encryption at Rest" in r.text
    assert "Upstream keys:" in r.text
    assert "Provider credentials:" in r.text


async def test_missing_encryption_key_returns_actionable_503_for_upstream_keys(
    client, tmp_path, monkeypatch
):
    from cryptography.fernet import Fernet

    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret = "sk-proj-encrypted-upstream-secret"
    await create_upstream_key(db_path, provider_id="openai", key_value=secret)
    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY")

    response = await client.get("/dashboard/api/inventory/keys")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "credential_encryption_error"
    assert "INVENTORY_ENCRYPTION_KEY" in error["message"]
    assert "Verify INVENTORY_ENCRYPTION_KEY" in error["hint"]
    assert secret not in response.text


async def test_wrong_encryption_key_returns_actionable_503_for_providers(
    client, tmp_path, monkeypatch
):
    from cryptography.fernet import Fernet

    from janus.storage.database import init_db
    from janus.storage.providers_db import create_provider

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret = "sk-encrypted-provider-secret"
    await create_provider(
        db_path,
        {
            "id": "secure",
            "prefix": "secure",
            "api_type": "openai_compat",
            "base_url": "https://secure.example/v1",
            "api_key": secret,
            "models": [],
        },
    )
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

    response = await client.get("/dashboard/providers")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "credential_encryption_error"
    assert "Failed to decrypt stored credential" in error["message"]
    assert "Verify INVENTORY_ENCRYPTION_KEY" in error["hint"]
    assert secret not in response.text


async def test_invalid_encryption_key_returns_actionable_503_on_write(client, monkeypatch):
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", "not-a-fernet-key")

    response = await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "sk-proj-new-secret", "provider_id": "openai"},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "credential_encryption_error"
    assert "invalid; expected a Fernet key" in error["message"]
    assert "sk-proj-new-secret" not in response.text


async def test_inventory_encrypt_action_covers_provider_credentials(client, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from janus.storage.database import get_connection, init_db
    from janus.storage.providers_db import create_provider

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-provider",
            "models": [],
        },
    )
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

    r = await client.post("/dashboard/api/inventory/encrypt-keys")

    assert r.status_code == 200
    assert "Encrypted 0 upstream key(s) and 1 provider credential(s)" in r.text
    async with get_connection(db_path) as db:
        async with db.execute("SELECT api_key FROM providers WHERE id = 'openai'") as cur:
            row = await cur.fetchone()
    assert row["api_key"].startswith("enc:v1:")


async def test_inventory_keys_has_reidentify_and_import_links(client):
    r = await client.get("/dashboard/inventory/keys")
    assert r.status_code == 200
    assert "Re-identify" in r.text
    assert "/dashboard/inventory/import" in r.text


async def test_inventory_import_upload(client):
    import json

    payload = json.dumps(
        [{"key_value": "sk-proj-" + "i" * 16, "provider_id": "openai", "status": "active"}]
    )
    r = await client.post(
        "/dashboard/api/inventory/import",
        files={"export_file": ("export.json", payload, "application/json")},
        data={"verify": "true"},
    )
    assert r.status_code == 200
    assert "Imported 1" in r.text
    assert "Verification Summary" in r.text


async def test_inventory_reclassify_preview(client):
    r = await client.post(
        "/dashboard/api/inventory/reclassify?dry=true&scope=invalid",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Re-identify Invalid Keys" in r.text


async def test_inventory_keys_json_pagination(client):
    for idx in range(3):
        await client.post(
            "/dashboard/api/inventory/keys",
            data={"keys_text": f"gsk_{idx}" + "x" * 16, "provider_id": "groq"},
        )
    listing = await client.get(
        "/dashboard/api/inventory/keys?limit=2&offset=0&sort=credits&dir=desc"
    )
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 3
    assert len(payload["keys"]) == 2


async def test_inventory_key_detail_endpoints(client):
    create = await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "sk-proj-detail-endpoint-key", "provider_id": "openai"},
    )
    assert create.status_code == 200
    export = await client.get("/dashboard/api/inventory/export")
    key_id = export.json()["keys"][0]["id"]

    detail = await client.get(f"/dashboard/api/inventory/keys/{key_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == key_id
    assert "models" in body
    assert "history" in body

    partial = await client.get(f"/dashboard/api/inventory/keys/{key_id}/partial")
    assert partial.status_code == 200
    assert "Key Detail" not in partial.text
    assert body["key_masked"] in partial.text or "sk-proj" in partial.text

    agent = await client.get(f"/dashboard/api/inventory/keys/{key_id}/json")
    assert agent.status_code == 200
    assert agent.json()["key_value"]


async def test_inventory_best_keys_endpoint(client):
    await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "sk-proj-best-endpoint-key", "provider_id": "openai"},
    )
    response = await client.get("/dashboard/api/inventory/best-keys")
    assert response.status_code == 200
    assert "bestKeys" in response.json()


async def test_inventory_export_provider_filter(client):
    await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "gsk_" + "y" * 16, "provider_id": "groq"},
    )
    await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "sk-proj-" + "z" * 16, "provider_id": "openai"},
    )
    export = await client.get("/dashboard/api/inventory/export?provider_id=groq")
    assert export.status_code == 200
    payload = export.json()
    assert payload["count"] == 1
    assert payload["keys"][0]["provider_id"] == "groq"
    assert "attachment" in export.headers.get("content-disposition", "")


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
        data={"keys_text": "sk-proj-" + "t" * 16, "provider_id": "openai"},
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
        data={"keys_text": "gsk_" + "x" * 16, "provider_id": "groq"},
    )
    assert create.status_code == 200
    assert "Validation in progress" in create.text
    assert 'hx-trigger="every 3s"' in create.text

    partial = await client.get("/dashboard/api/inventory/keys/partial")
    assert partial.status_code == 200


async def test_inventory_submit_status_endpoint(client):
    create = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": "sk-proj-status-test", "provider_id": "openai"},
    )
    assert create.status_code == 200
    assert 'hx-trigger="every 3s"' in create.text

    export = await client.get("/dashboard/api/inventory/export")
    key_id = export.json()["keys"][0]["id"]

    status = await client.get(f"/dashboard/api/inventory/submit/status?ids={key_id}")
    assert status.status_code == 200
    assert "pending_validation" in status.text


async def test_inventory_delete_key(client):
    create = await client.post(
        "/dashboard/api/inventory/keys",
        data={"keys_text": "gsk_" + "x" * 16, "provider_id": "groq"},
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
