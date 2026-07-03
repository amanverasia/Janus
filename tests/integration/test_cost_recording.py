import aiosqlite
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


@pytest.mark.asyncio
async def test_cost_recorded_for_non_streaming(tmp_path):
    db_path = tmp_path / "test.db"

    provider = ProviderConfig(
        id="test-openai",
        prefix="test",
        api_type="openai_compat",
        base_url="https://api.test.com/v1",
        api_key="sk-test",
        models=["gpt-4o"],
    )
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path, require_api_key=False),
        providers=[provider],
    )
    app = create_app(config=config)
    app.state.db_path = db_path
    await _seed_and_reload(app)

    mock_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }

    with respx.mock(base_url="https://api.test.com/v1") as mock:
        mock.post("/chat/completions").respond(200, json=mock_response)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "test/gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT cost, input_tokens, output_tokens FROM usage WHERE model = 'gpt-4o'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["cost"] > 0
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 50


@pytest.mark.asyncio
@respx.mock
async def test_cost_recorded_for_streaming(tmp_path):
    db_path = tmp_path / "test.db"

    provider = ProviderConfig(
        id="test-openai",
        prefix="test",
        api_type="openai_compat",
        base_url="https://api.test.com/v1",
        api_key="sk-test",
        models=["gpt-4o"],
    )
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path, require_api_key=False),
        providers=[provider],
    )
    app = create_app(config=config)
    app.state.db_path = db_path
    await _seed_and_reload(app)

    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello!"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","choices":[],'
        '"usage":{"prompt_tokens":80,"completion_tokens":20,"total_tokens":100}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.test.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "test/gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_bytes():
                pass

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT cost, input_tokens, output_tokens FROM usage WHERE model = 'gpt-4o'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["cost"] > 0
    assert rows[0]["input_tokens"] == 80
    assert rows[0]["output_tokens"] == 20


@pytest.mark.asyncio
@respx.mock
async def test_cost_recorded_for_streaming_tiktoken_fallback(tmp_path):
    db_path = tmp_path / "test.db"

    provider = ProviderConfig(
        id="test-openai",
        prefix="test",
        api_type="openai_compat",
        base_url="https://api.test.com/v1",
        api_key="sk-test",
        models=["gpt-4o"],
    )
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path, require_api_key=False),
        providers=[provider],
    )
    app = create_app(config=config)
    app.state.db_path = db_path
    await _seed_and_reload(app)

    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello world!"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.test.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "test/gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_bytes():
                pass

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT input_tokens, output_tokens FROM usage WHERE model = 'gpt-4o'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["output_tokens"] > 0
