import time

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
CHAT_URL = "https://api.githubcopilot.com/chat/completions"


async def _seed_and_reload(app) -> None:
    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.storage.database import init_db, seed_from_config

    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)


@pytest.fixture
async def app(tmp_path):
    provider = ProviderConfig(
        id="copilot",
        prefix="copilot",
        api_type="github_copilot",
        base_url="https://api.githubcopilot.com",
        api_key="gho_test_token",
        models=["gpt-4o"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _mock_copilot_upstream() -> None:
    respx.get(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"token": "copilot-session", "expires_at": time.time() + 1800},
        )
    )
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello from Copilot!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_copilot_provider_routes_chat(app):
    _mock_copilot_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "copilot/gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Hello from Copilot!"


@pytest.mark.asyncio
@respx.mock
async def test_copilot_bad_token_falls_through_to_503(app):
    respx.get(TOKEN_URL).mock(return_value=httpx.Response(401, json={"message": "bad credentials"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "copilot/gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_oauth_start_endpoint(app):
    respx.post("https://github.com/login/device/code").mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dc123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/oauth/copilot/start")
        assert r.status_code == 200
        data = r.json()
        assert data["user_code"] == "ABCD-1234"
        assert data["device_code"] == "dc123"


@pytest.mark.asyncio
@respx.mock
async def test_oauth_poll_endpoint(app):
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "gho_new_token"})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/oauth/copilot/poll", data={"device_code": "dc123"})
        assert r.status_code == 200
        assert r.json() == {"status": "success", "access_token": "gho_new_token"}

        r = await client.post("/dashboard/api/oauth/copilot/poll", data={})
        assert r.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_create_copilot_provider_via_dashboard(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    app = create_app(config=cfg)
    _mock_copilot_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/providers",
            data={
                "id": "copilot",
                "prefix": "copilot",
                "api_type": "github_copilot",
                "base_url": "https://api.githubcopilot.com",
                "api_key": "gho_test_token",
                "models": "gpt-4o, claude-sonnet-4",
            },
        )
        assert r.status_code == 200

        payload = {
            "model": "copilot/gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Hello from Copilot!"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_models_github_copilot(app):
    respx.get(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"token": "copilot-session", "expires_at": time.time() + 1800}
        )
    )
    respx.get("https://api.githubcopilot.com/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "o4-mini"}]})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/providers/fetch-models",
            data={
                "api_type": "github_copilot",
                "base_url": "https://api.githubcopilot.com",
                "api_key": "gho_test_token",
            },
        )
        assert r.status_code == 200
        assert r.json()["models"] == ["gpt-4o", "o4-mini"]


@pytest.mark.asyncio
async def test_providers_page_lists_copilot_catalog(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "GitHub Copilot" in r.text
        assert "github_copilot" in r.text
        assert "startCopilotAuth" in r.text
