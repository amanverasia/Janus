from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn import Config

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.api_keys import create_key
from janus.storage.database import init_db
from janus.storage.settings import get_setting


@pytest.fixture
async def proxy_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.10")
    app = create_app(config=JanusConfig(server=ServerSettings(data_dir=tmp_path)))
    await init_db(app.state.db_path)
    key, _ = await create_key(app.state.db_path, "proxy-test", can_login=True)
    app.state._dashboard_db_ready = True
    config = Config(app, log_config=None, lifespan="off")
    config.load()

    async def tls_terminated_backend(scope, receive, send):
        scope["scheme"] = "http"
        await config.loaded_app(scope, receive, send)

    return app, tls_terminated_backend, key


@pytest.mark.parametrize("authority", ["gateway.example", "gateway.example:8443"])
async def test_trusted_https_proxy_login_cookie_mutation_and_endpoint(proxy_app, authority):
    app, served, key = proxy_app
    async with AsyncClient(
        transport=ASGITransport(app=served, client=("192.0.2.10", 12345)),
        base_url=f"https://{authority}",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.20"},
    ) as client:
        login = await client.post(
            "/dashboard/login",
            data={"api_key": key},
            headers={"Origin": f"https://{authority}"},
        )
        assert login.status_code == 303
        cookie = login.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert "SameSite=lax" in cookie and "Path=/dashboard" in cookie

        mutation = await client.post(
            "/dashboard/api/settings",
            headers={"Origin": f"https://{authority}"},
            data={"key": "server_request_logging", "value": "true"},
        )
        assert mutation.status_code == 200
        assert await get_setting(app.state.db_path, "server_request_logging") == "true"

        tools = await client.get("/dashboard/api/v2/state/tools")
        assert tools.status_code == 200
        assert tools.json()["data"]["base_url"] == f"https://{authority}/v1"


@pytest.mark.parametrize(
    "origin",
    [
        "http://gateway.example",
        "https://evil.example",
        "https://gateway.example:8443",
        "null",
        "",
        "https://gateway.example/path",
        "https://gateway.example?",
        "https://gateway.example#",
        "https://gateway.example:invalid",
        "https://gateway.example:65536",
        "https://user@gateway.example",
        "https://gateway.example\\",
        " https://gateway.example",
        "https://gateway.example https://evil.example",
    ],
)
async def test_https_proxy_rejects_downgrade_foreign_and_malformed_origins(proxy_app, origin):
    app, served, key = proxy_app
    async with AsyncClient(
        transport=ASGITransport(app=served, client=("192.0.2.10", 12345)),
        base_url="http://gateway.example",
    ) as client:
        response = await client.post(
            "/dashboard/api/settings",
            headers={
                "Origin": origin,
                "X-Forwarded-Proto": "https",
                "Authorization": f"Bearer {key}",
            },
            data={"key": "server_request_logging", "value": "true"},
        )
        assert response.status_code == 403
        assert await get_setting(app.state.db_path, "server_request_logging") is None


async def test_proxy_rejects_duplicate_origins(proxy_app):
    _, served, key = proxy_app
    async with AsyncClient(
        transport=ASGITransport(app=served, client=("192.0.2.10", 12345)),
        base_url="http://gateway.example",
    ) as client:
        response = await client.post(
            "/dashboard/logout",
            headers=[
                ("Origin", "https://gateway.example"),
                ("Origin", "https://evil.example"),
                ("X-Forwarded-Proto", "https"),
                ("Authorization", f"Bearer {key}"),
            ],
        )
        assert response.status_code == 403


async def test_untrusted_client_cannot_spoof_proxy_scheme_or_host(proxy_app):
    _, served, key = proxy_app
    async with AsyncClient(
        transport=ASGITransport(app=served, client=("198.51.100.30", 12345)),
        base_url="http://gateway.example",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "evil.example",
            "Forwarded": "proto=https;host=evil.example",
            "Authorization": f"Bearer {key}",
        },
    ) as client:
        rejected = await client.post(
            "/dashboard/login",
            data={"api_key": key},
            headers={"Origin": "https://gateway.example"},
        )
        assert rejected.status_code == 403
        direct = await client.post(
            "/dashboard/login",
            data={"api_key": key},
            headers={"Origin": "http://gateway.example"},
        )
        assert direct.status_code == 303
        assert "Secure" not in direct.headers["set-cookie"]
        tools = await client.get("/dashboard/api/v2/state/tools")
        assert tools.json()["data"]["base_url"] == "http://gateway.example/v1"


async def test_trusted_scheme_metadata_does_not_trust_forwarded_host(proxy_app):
    _, served, key = proxy_app
    async with AsyncClient(
        transport=ASGITransport(app=served, client=("192.0.2.10", 12345)),
        base_url="http://gateway.example",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "evil.example",
            "Forwarded": "proto=http;host=evil.example",
            "Authorization": f"Bearer {key}",
        },
    ) as client:
        rejected = await client.post(
            "/dashboard/logout", headers={"Origin": "https://evil.example"}
        )
        assert rejected.status_code == 403
        accepted = await client.post(
            "/dashboard/logout", headers={"Origin": "https://gateway.example"}
        )
        assert accepted.status_code == 303
