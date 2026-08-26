from __future__ import annotations

import hashlib
import hmac
import re
import time

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.api_keys import create_key
from janus.storage.database import init_db
from janus.storage.settings import set_setting


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    return create_app(config=cfg)


def _remote_transport(app) -> ASGITransport:
    return ASGITransport(app=app, client=("192.0.2.10", 43210))


def _legacy_password_hash(password: str) -> str:
    salt = "0123456789abcdef0123456789abcdef"
    iterations = 100_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def _legacy_session_token(secret: str, username: str) -> str:
    expires_at = int(time.time()) + 3600
    payload = f"{username}:{expires_at}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


@pytest.mark.asyncio
async def test_api_key_required_from_db_setting(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "server_require_api_key", "true")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/models")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_key_db_setting_allows_valid_key(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "server_require_api_key", "true")
    key, _ = await create_key(db_path, "ok")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_api_key_login_sets_cookie_and_authenticates(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, "dashboard-user", can_login=True)
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
    ) as client:
        login_response = await client.post(
            "/dashboard/login",
            data={"api_key": key, "next": "/dashboard/keys"},
            follow_redirects=False,
        )
        assert login_response.status_code == 303
        assert login_response.headers["location"] == "/dashboard/ui/keys"
        assert login_response.cookies["janus_dashboard_key"] == key
        assert "HttpOnly" in login_response.headers["set-cookie"]

        dashboard_response = await client.get("/dashboard/ui/keys")
        assert dashboard_response.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_login_defaults_and_invalid_next_use_canonical_ui(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, "dashboard-user", can_login=True)
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
    ) as client:
        page_response = await client.get("/dashboard/login")
        default_response = await client.post(
            "/dashboard/login",
            data={"api_key": key},
            follow_redirects=False,
        )
        invalid_response = await client.post(
            "/dashboard/login",
            data={"api_key": key, "next": "https://example.test/steal"},
            follow_redirects=False,
        )

    assert 'name="next" value="/dashboard/ui"' in page_response.text
    assert default_response.headers["location"] == "/dashboard/ui"
    assert invalid_response.headers["location"] == "/dashboard/ui"


@pytest.mark.asyncio
async def test_dashboard_api_key_login_rejects_key_without_login_access(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, "api-only", can_login=False)
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
    ) as client:
        response = await client.post(
            "/dashboard/login",
            data={"api_key": key, "next": "/dashboard"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "cannot access the dashboard" in response.text
        assert "janus_dashboard_key" not in response.cookies


@pytest.mark.asyncio
async def test_dashboard_loopback_access_requires_api_key(app):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 43210)),
        base_url="http://127.0.0.1",
    ) as client:
        page_response = await client.get(
            "/dashboard",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        assert page_response.status_code == 303
        assert page_response.headers["location"].startswith("/dashboard/login")

        api_response = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_request_logging", "value": "true"},
            headers={"Accept": "application/json"},
        )
        assert api_response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_username_password_submission_never_authenticates(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "dashboard_username", "hett")
    await set_setting(db_path, "dashboard_password_hash", _legacy_password_hash("valid-password"))
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
    ) as client:
        response = await client.post(
            "/dashboard/login",
            data={
                "username": "hett",
                "password": "valid-password",
                "next": "/dashboard",
            },
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "janus_dashboard_session" not in response.cookies
        assert "janus_dashboard_key" not in response.cookies


@pytest.mark.asyncio
async def test_dashboard_legacy_session_cookie_never_authenticates(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    secret = "legacy-session-secret"
    await set_setting(db_path, "dashboard_username", "hett")
    await set_setting(db_path, "dashboard_password_hash", _legacy_password_hash("valid-password"))
    await set_setting(db_path, "dashboard_session_secret", secret)
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
        cookies={"janus_dashboard_session": _legacy_session_token(secret, "hett")},
    ) as client:
        response = await client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/dashboard/login")


@pytest.mark.asyncio
async def test_login_and_settings_ui_expose_only_api_key_login(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, "dashboard-user", can_login=True)
    await set_setting(db_path, "dashboard_username", "hett")
    await set_setting(db_path, "dashboard_password_hash", _legacy_password_hash("valid-password"))
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://janus.test",
        cookies={"janus_dashboard_key": key},
    ) as client:
        login_response = await client.get("/dashboard/login")
        assert login_response.status_code == 200
        assert re.search(r'<input[^>]+name="api_key"', login_response.text)
        assert 'name="username"' not in login_response.text
        assert 'name="password"' not in login_response.text
        assert "Username &amp; Password" not in login_response.text

        settings_response = await client.get("/dashboard/api/v2/state/settings")
        assert settings_response.status_code == 200
        assert "dashboard_username" not in settings_response.json()["data"]["values"]
        assert "dashboard_password_hash" not in settings_response.json()["data"]["values"]
        assert "hett" not in settings_response.text


@pytest.mark.asyncio
async def test_tools_page_uses_request_base_url(app, tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, "dashboard-user", can_login=True)
    app.state._dashboard_db_ready = True

    async with AsyncClient(
        transport=_remote_transport(app),
        base_url="http://myhost:9999",
    ) as client:
        response = await client.get(
            "/dashboard/api/v2/state/tools",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["base_url"] == "http://myhost:9999/v1"
