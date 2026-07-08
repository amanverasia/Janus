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


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_thinking_streams_to_client(anthropic_app):
    """Claude Code -> Anthropic provider: thinking blocks + signature reach the client."""
    upstream_sse = (
        'data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514",'
        '"usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"thinking","thinking":""}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"let me reason"}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"signature_delta","signature":"SIG123"}}\n\n'
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"content_block_start","index":1,'
        '"content_block":{"type":"text","text":""}}\n\n'
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"Answer"}}\n\n'
        'data: {"type":"content_block_stop","index":1}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":4}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://an.local/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=upstream_sse.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=anthropic_app), base_url="http://test"
    ) as client:
        payload = {
            "model": "anthropic/claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "thinking": {"type": "enabled", "budget_tokens": 2000},
            "messages": [{"role": "user", "content": "hard question"}],
            "stream": True,
        }
        body = b""
        async with client.stream("POST", "/v1/messages", json=payload) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_bytes():
                body += chunk

    # Thinking content and signature survived the full pivot back to the client.
    assert b"thinking_delta" in body
    assert b"let me reason" in body
    assert b"signature_delta" in body
    assert b"SIG123" in body
    assert b"Answer" in body


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_thinking_forwarded_upstream(anthropic_app):
    """The thinking request param is forwarded to the upstream provider."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    respx.post("https://an.local/v1/messages").mock(side_effect=_capture)

    async with AsyncClient(
        transport=ASGITransport(app=anthropic_app), base_url="http://test"
    ) as client:
        payload = {
            "model": "anthropic/claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "thinking": {"type": "enabled", "budget_tokens": 2000},
            "messages": [{"role": "user", "content": "q"}],
        }
        r = await client.post("/v1/messages", json=payload)
        assert r.status_code == 200

    assert captured.get("thinking") == {"type": "enabled", "budget_tokens": "2000"}
