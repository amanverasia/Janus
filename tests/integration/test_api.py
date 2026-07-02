import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings


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


@pytest.mark.asyncio
async def test_models_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert any(m["id"] == "test/test-m1" for m in data["data"])


@pytest.mark.asyncio
async def test_health(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_nonstream(app):
    respx.post("https://fake.local/v1/chat/completions").mock(
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
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["choices"][0]["message"]["content"] == "Hello!"


@pytest.mark.asyncio
@respx.mock
async def test_messages_endpoint_nonstream(app):
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "test-m1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Bonjour!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/messages", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert data["content"][0]["text"] == "Bonjour!"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_stream(app):
    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},'
        '"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
            assert b"Hello" in body
            assert b"[DONE]" in body


@pytest.mark.asyncio
async def test_unknown_model_error(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "no/such-model",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_gemini_inbound_nonstream(app):
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "test-m1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello from Gemini!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        }
        r = await client.post("/v1beta/models/test/test-m1:generateContent", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["candidates"][0]["content"]["parts"][0]["text"] == "Hello from Gemini!"
        assert data["usageMetadata"]["promptTokenCount"] == 4


@pytest.mark.asyncio
@respx.mock
async def test_gemini_inbound_stream(app):
    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},'
        '"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        }
        async with client.stream(
            "POST", "/v1beta/models/test/test-m1:streamGenerateContent", json=payload
        ) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
            assert b"Hello" in body


@pytest.mark.asyncio
async def test_gemini_inbound_bad_action(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1beta/models/test/test-m1:bogusAction", json={"contents": []})
        assert r.status_code == 404


# --- Phase 2 tests ---


@pytest.mark.asyncio
@respx.mock
async def test_fallback_on_429(tmp_path):
    provider1 = ProviderConfig(
        id="t1",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="k1",
        models=["m1"],
    )
    provider2 = ProviderConfig(
        id="t2",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake2.local/v1",
        api_key="k2",
        models=["m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider1, provider2],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://fake2.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "model": "m1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_all_providers_exhaustured_returns_503(tmp_path):
    provider = ProviderConfig(
        id="t1",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="k1",
        models=["m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "down"})
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "model": "test/m1",
                "messages": [{"role": "user", "content": "hi"}],
            }
            r = await client.post("/v1/chat/completions", json=payload)
            assert r.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_combo_expansion(tmp_path):
    provider = ProviderConfig(
        id="a",
        prefix="a",
        api_type="openai_compat",
        base_url="https://a.local/v1",
        api_key="k",
        models=["b"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider],
        combos=[ComboConfig(name="stk", models=["a/b"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "model": "b",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "combo works",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "stk", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "combo works"


@pytest.mark.asyncio
async def test_models_lists_combos(tmp_path):
    provider = ProviderConfig(
        id="a",
        prefix="a",
        api_type="openai_compat",
        base_url="https://a.local/v1",
        api_key="k",
        models=["b"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider],
        combos=[ComboConfig(name="stk", models=["a/b"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert "a/b" in ids
        assert "stk" in ids


@pytest.mark.asyncio
@respx.mock
async def test_rtk_compresses_tool_result_before_provider(tmp_path):
    """RTK should compress tool_result content before it reaches the provider."""
    from janus.config.schema import TokenSaverConfig, TokenSaverSettings

    provider = ProviderConfig(
        id="t",
        prefix="t",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="k",
        models=["m"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider],
        token_savers=TokenSaverConfig(rtk=TokenSaverSettings(enabled=True)),
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    long_diff = "diff --git a/f.py b/f.py\nindex 111..222 100644\n" + "line\n" * 300
    captured: dict = {}

    def capture(request):
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "model": "m",
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

    respx.post("https://fake.local/v1/chat/completions").mock(side_effect=capture)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "t/m",
            "messages": [
                {"role": "user", "content": "fix"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "diff", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": long_diff},
            ],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        tool_msg = captured["messages"][-1]
        assert len(tool_msg["content"]) < len(long_diff)


@pytest.mark.asyncio
@respx.mock
async def test_usage_recorded_after_request(tmp_path):
    """Usage should be recorded to DB after a successful non-streaming request."""
    from janus.storage.usage import get_usage_stats

    provider = ProviderConfig(
        id="t",
        prefix="t",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="k",
        models=["m"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/chat/completions",
            json={"model": "t/m", "messages": [{"role": "user", "content": "hi"}]},
        )

    stats = await get_usage_stats(app.state.db_path)
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 10
    assert stats["total_output_tokens"] == 5
