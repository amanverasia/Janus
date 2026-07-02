import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.database import init_db
from janus.storage.settings import set_setting


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    return create_app(config=cfg)


@pytest.mark.asyncio
async def test_api_key_required_from_db_setting(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "server_require_api_key", "true")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_api_key_db_setting_allows_valid_key(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "server_require_api_key", "true")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_r = await client.post("/dashboard/api/keys", data={"name": "ok"})
        import re

        match = re.search(r"sk-janus-[a-f0-9]+", create_r.text)
        assert match is not None
        key = match.group(0)
        r = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_login_sets_cookie(app, tmp_path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_r = await client.post("/dashboard/api/keys", data={"name": "dash"})
        import re

        match = re.search(r"sk-janus-[a-f0-9]+", create_r.text)
        assert match is not None
        key = match.group(0)
        login_r = await client.post(
            "/dashboard/login",
            data={"api_key": key, "next": "/dashboard/keys"},
            follow_redirects=False,
        )
        assert login_r.status_code == 303
        assert "janus_dashboard_key" in login_r.cookies


@pytest.mark.asyncio
async def test_tools_page_uses_request_base_url(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://myhost:9999") as client:
        r = await client.get("/dashboard/tools")
        assert r.status_code == 200
        assert "http://myhost:9999/v1" in r.text
