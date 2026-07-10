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


async def _enable_caveman(app, level: str | None = None) -> None:
    from janus.dashboard.reload import reload_savers

    await set_setting(app.state.db_path, "saver_caveman_enabled", "true")
    if level is not None:
        await set_setting(app.state.db_path, "saver_caveman_level", level)
    await reload_savers(app)


@pytest.mark.asyncio
@respx.mock
async def test_caveman_lite_injects_prompt(app):
    from janus.tokensavers.caveman import PROMPTS

    upstream = _mock_upstream()
    await _enable_caveman(app, "lite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    import json

    sent = json.loads(upstream.calls.last.request.content)
    system_msgs = [m for m in sent["messages"] if m["role"] == "system"]
    assert system_msgs[0]["content"] == PROMPTS["lite"]


@pytest.mark.asyncio
@respx.mock
async def test_caveman_ultra_injects_prompt(app):
    from janus.tokensavers.caveman import PROMPTS

    upstream = _mock_upstream()
    await _enable_caveman(app, "ultra")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    import json

    sent = json.loads(upstream.calls.last.request.content)
    system_msgs = [m for m in sent["messages"] if m["role"] == "system"]
    assert system_msgs[0]["content"] == PROMPTS["ultra"]


@pytest.mark.asyncio
async def test_caveman_invalid_db_level_falls_back_to_full(app):
    from janus.tokensavers.caveman import PROMPTS

    await _enable_caveman(app, "bogus-level")
    savers = app.state.saver_pipeline._savers
    caveman = next(s for s in savers if type(s).__name__ == "CavemanSaver")
    assert caveman.level == "full"
    assert PROMPTS[caveman.level]


@pytest.mark.asyncio
async def test_savers_page_shows_caveman_levels(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/savers")
        assert r.status_code == 200
        assert "saver_caveman_level" in r.text
        assert "Ultra" in r.text
