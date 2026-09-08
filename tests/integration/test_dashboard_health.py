import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings

AUTH_KEY = "dashboard-auth-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    return create_app(
        config=JanusConfig(
            server=ServerSettings(port=0, data_dir=tmp_path),
            providers=[
                ProviderConfig(
                    id="test-provider",
                    prefix="test",
                    api_type="openai_compat",
                    base_url="https://provider.example/v1",
                    api_key="provider-super-secret",
                    models=["model-1"],
                )
            ],
            combos=[ComboConfig(name="test-combo", models=["test/model-1"])],
            api_keys=[AUTH_KEY],
        )
    )


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 4321)),
        base_url="http://test",
    )


async def test_health_requires_dashboard_authentication(app):
    async with client_for(app) as client:
        r = await client.get("/dashboard/api/v2/health")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/dashboard/login")


async def test_health_contract_is_state_backed_and_secret_free(app):
    async with client_for(app) as client:
        r = await client.get("/dashboard/api/v2/health", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"
    body = r.json()
    assert body["status"] == "online"
    assert body["database"] == {"reachable": True}
    assert body["providers"] == {"total": 1, "enabled": 1}
    assert set(body["schedulers"]) == {"inventory", "pricing"}
    assert body["schedulers"]["inventory"] in {"unknown", "running", "disabled", "stopped"}
    assert isinstance(body["cooldown_count"], int)
    assert body["last_inventory_check_age_s"] is None
    assert body["version"] == app.version
    assert body["identity"]["kind"] == "config_key"
    assert body["identity"]["label"].startswith("Config (")
    assert "dashboard-auth-secret" not in r.text
    assert "provider-super-secret" not in r.text


async def test_health_identity_uses_stored_db_key_label(app):
    from janus.storage.api_keys import create_key

    async with client_for(app) as client:
        await client.get("/dashboard/api/v2/health", headers=AUTH_HEADERS)
        plaintext, record = await create_key(app.state.db_path, "ops-laptop")
        r = await client.get(
            "/dashboard/api/v2/health",
            headers={"Authorization": f"Bearer {plaintext}", "Accept": "application/json"},
        )
    assert r.status_code == 200
    identity = r.json()["identity"]
    assert identity == {"kind": "api_key", "label": "ops-laptop"}
    assert plaintext not in r.text


async def test_health_reports_last_inventory_check_age(app):
    from janus.storage.database import get_connection
    from janus.storage.upstream_keys import create_upstream_key

    async with client_for(app) as client:
        await client.get("/dashboard/api/v2/health", headers=AUTH_HEADERS)
        await create_upstream_key(
            app.state.db_path, provider_id="test", key_value="sk-prob-1234567890abcdef"
        )
        async with get_connection(app.state.db_path) as db:
            await db.execute(
                "UPDATE upstream_keys SET last_checked_at = datetime('now', '-10 minutes')"
            )
            await db.commit()
        r = await client.get("/dashboard/api/v2/health", headers=AUTH_HEADERS)
    age = r.json()["last_inventory_check_age_s"]
    assert age is not None
    assert 590 <= age <= 660
