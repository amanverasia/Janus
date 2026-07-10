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
async def anthropic_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="an",
                prefix="anthropic",
                api_type="anthropic",
                base_url="https://an.local",
                api_key="sk-an",
                models=["claude-sonnet-4-20250514"],
            )
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture
async def transport_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="ds-transport",
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds-openai.local/v1",
                api_key="sk-ds",
                models=["claude-sonnet-4-20250514"],
                transports={"anthropic": "https://ds-anthropic.local/v1"},
            )
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.mark.asyncio
@respx.mock
async def test_passthrough_same_format_skips_canonical(anthropic_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "pong"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    respx.post("https://an.local/v1/messages").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=anthropic_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/messages",
            json={
                "model": "anthropic/claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["content"][0]["text"] == "pong"

    # With A1 fix: build_upstream_request produces structured Anthropic blocks
    assert captured["body"]["messages"][0]["content"][0]["text"] == "ping"
    assert captured["body"]["max_tokens"] == 1024


@pytest.mark.asyncio
@respx.mock
async def test_transport_routes_to_matching_format_endpoint(transport_app):
    route_oa = respx.post("https://ds-openai.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    route_an = respx.post("https://ds-anthropic.local/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/messages",
            json={
                "model": "ds/claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert route_an.called
    assert not route_oa.called


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_transport_sets_x_api_key(transport_app):
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    respx.post("https://ds-anthropic.local/v1/messages").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/v1/messages",
            json={
                "model": "ds/claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert captured["headers"].get("x-api-key") == "sk-ds"
    assert captured["headers"].get("anthropic-version") == "2023-06-01"
