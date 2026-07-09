import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings


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
async def openrouter_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openrouter",
                prefix="openrouter",
                api_type="openai_compat",
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-or-test",
                models=["openai/gpt-4o-mini", "anthropic/claude-opus-4.8"],
            )
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_sends_attribution_headers(openrouter_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=openrouter_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "openrouter/openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert captured["body"]["model"] == "openai/gpt-4o-mini"
    assert captured["headers"].get("http-referer") == "https://janus.local"
    assert captured["headers"].get("x-title") == "Janus"
    assert captured["headers"].get("authorization") == "Bearer sk-or-test"


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_nested_model_passthrough(openrouter_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=openrouter_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "openrouter/anthropic/claude-opus-4.8",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert captured["body"]["model"] == "anthropic/claude-opus-4.8"
