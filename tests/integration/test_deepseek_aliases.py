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
async def deepseek_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="deepseek",
                prefix="deepseek",
                api_type="openai_compat",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-ds",
                models=[
                    "deepseek-v4-pro",
                    "deepseek-v4-pro-max",
                    "deepseek-v4-pro-none",
                    "deepseek-v4-flash",
                ],
                transports={"anthropic": "https://api.deepseek.com/anthropic/v1"},
            )
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.mark.asyncio
@respx.mock
async def test_deepseek_v4_pro_max_maps_upstream_and_thinking(deepseek_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
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

    respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=deepseek_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek/deepseek-v4-pro-max",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["reasoning_effort"] == "max"


@pytest.mark.asyncio
@respx.mock
async def test_deepseek_v4_pro_none_disables_thinking(deepseek_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
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

    respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=deepseek_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "deepseek/deepseek-v4-pro-none",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in captured["body"]
