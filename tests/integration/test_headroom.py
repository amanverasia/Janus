import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.settings import set_setting


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
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _mock_upstream() -> respx.Route:
    return respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "test-m1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )
    )


async def _enable_headroom(app) -> None:
    from janus.dashboard.reload import reload_savers

    await set_setting(app.state.db_path, "saver_headroom_enabled", "true")
    await reload_savers(app)


@pytest.mark.asyncio
@respx.mock
async def test_headroom_compresses_before_upstream(app):
    upstream = _mock_upstream()
    respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(
            200,
            json={"messages": [{"role": "user", "content": "compressed prompt"}]},
        )
    )
    await _enable_headroom(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "a very long prompt " * 50}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    import json

    sent = json.loads(upstream.calls.last.request.content)
    user_msgs = [m for m in sent["messages"] if m["role"] == "user"]
    assert user_msgs[0]["content"] == "compressed prompt"


@pytest.mark.asyncio
@respx.mock
async def test_headroom_down_fails_open(app):
    upstream = _mock_upstream()
    respx.post("http://localhost:8787/v1/compress").mock(side_effect=httpx.ConnectError("refused"))
    await _enable_headroom(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "original prompt"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Hello!"

    import json

    sent = json.loads(upstream.calls.last.request.content)
    user_msgs = [m for m in sent["messages"] if m["role"] == "user"]
    assert user_msgs[0]["content"] == "original prompt"


@pytest.mark.asyncio
@respx.mock
async def test_headroom_disabled_by_default(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
    assert not any(call.request.url.host == "localhost" for call in respx.calls)


@pytest.mark.asyncio
async def test_savers_page_shows_headroom(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/savers")
        assert r.status_code == 200
        assert "Headroom" in r.text
        assert "saver_headroom_enabled" in r.text
