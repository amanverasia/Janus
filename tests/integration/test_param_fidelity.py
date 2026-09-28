from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings

_UPSTREAM_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "test-m1",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

_SAMPLING_PARAMS: dict[str, object] = {
    "seed": 42,
    "n": 2,
    "presence_penalty": 0.5,
    "frequency_penalty": -0.25,
    "logit_bias": {"50256": -100},
    "parallel_tool_calls": False,
    "logprobs": True,
    "top_logprobs": 5,
}


@pytest.fixture
async def app(tmp_path):
    provider = ProviderConfig(
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
        default_model="test-m1",
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
        combos=[ComboConfig(name="test-combo", models=["test/test-m1"])],
    )
    app = create_app(config=cfg)
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
    return app


@respx.mock
async def test_openai_sampling_params_reach_openai_compat_upstream(app) -> None:
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=Response(200, json=_UPSTREAM_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test/test-m1",
                "messages": [{"role": "user", "content": "hi"}],
                **_SAMPLING_PARAMS,
            },
        )
    assert r.status_code == 200
    assert route.called
    upstream = json.loads(route.calls[0].request.content)
    for name, value in _SAMPLING_PARAMS.items():
        assert upstream[name] == value, name


@respx.mock
async def test_max_completion_tokens_reaches_upstream(app) -> None:
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=Response(200, json=_UPSTREAM_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test/test-m1",
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 4096,
            },
        )
    assert r.status_code == 200
    upstream = json.loads(route.calls[0].request.content)
    assert upstream["max_tokens"] == 4096


@respx.mock
async def test_string_stop_reaches_upstream_as_list(app) -> None:
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=Response(200, json=_UPSTREAM_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test/test-m1",
                "messages": [{"role": "user", "content": "hi"}],
                "stop": "END",
            },
        )
    assert r.status_code == 200
    upstream = json.loads(route.calls[0].request.content)
    assert upstream["stop"] == ["END"]


@respx.mock
async def test_sampling_params_survive_combo_fallback_request(app) -> None:
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=Response(200, json=_UPSTREAM_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test-combo",
                "messages": [{"role": "user", "content": "hi"}],
                **_SAMPLING_PARAMS,
            },
        )
    assert r.status_code == 200
    upstream = json.loads(route.calls[0].request.content)
    for name, value in _SAMPLING_PARAMS.items():
        assert upstream[name] == value, name
    assert upstream["model"] == "test-m1"
