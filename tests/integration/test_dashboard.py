import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t",
                prefix="t",
                api_type="openai_compat",
                base_url="https://test.local/v1",
                api_key="k",
                models=["m1"],
            )
        ],
        combos=[ComboConfig(name="stk", models=["t/m1"])],
    )
    return create_app(config=cfg)


@pytest.mark.asyncio
async def test_dashboard_overview(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Janus" in r.text


@pytest.mark.asyncio
async def test_dashboard_providers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "t/" in r.text or "t" in r.text


@pytest.mark.asyncio
async def test_dashboard_combos(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/combos")
        assert r.status_code == 200
        assert "stk" in r.text


@pytest.mark.asyncio
async def test_dashboard_keys_page(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/keys")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_keys_create(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/keys", data={"name": "test-key"})
        assert r.status_code == 200
        assert "sk-janus-" in r.text


@pytest.mark.asyncio
async def test_dashboard_keys_revoke(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create a key first
        await client.post("/dashboard/api/keys", data={"name": "torevoke"})
        # Revoke it
        r = await client.delete("/dashboard/api/keys/1")
        assert r.status_code == 200
        assert "Revoked" in r.text or "revoked" in r.text.lower()


@pytest.mark.asyncio
async def test_dashboard_usage(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/usage")
        assert r.status_code == 200
