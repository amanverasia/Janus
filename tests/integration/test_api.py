import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry


@pytest.fixture
def registry():
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="test",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="sk-test",
            models=["test-m1"],
        )
    )
    return reg


@pytest.fixture
def config():
    return JanusConfig(server=ServerSettings(port=0, require_api_key=False))


@pytest.fixture
def app(registry, config):
    return create_app(registry, config)


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


# --- Phase 2 tests ---


@pytest.mark.asyncio
@respx.mock
async def test_fallback_on_429():
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="t1",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="k1",
            models=["m1"],
        )
    )
    reg.register(
        ProviderConfig(
            id="t2",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake2.local/v1",
            api_key="k2",
            models=["m1"],
        )
    )
    app = create_app(reg, JanusConfig(server=ServerSettings(port=0)))

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
async def test_all_providers_exhaustured_returns_503():
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="t1",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="k1",
            models=["m1"],
        )
    )
    app = create_app(reg, JanusConfig(server=ServerSettings(port=0)))

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
async def test_combo_expansion():
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="a",
            prefix="a",
            api_type="openai_compat",
            base_url="https://a.local/v1",
            api_key="k",
            models=["b"],
        )
    )
    reg.register_combo(ComboConfig(name="stk", models=["a/b"]))
    app = create_app(reg, JanusConfig(server=ServerSettings(port=0)))

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
async def test_models_lists_combos():
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="a",
            prefix="a",
            api_type="openai_compat",
            base_url="https://a.local/v1",
            api_key="k",
            models=["b"],
        )
    )
    reg.register_combo(ComboConfig(name="stk", models=["a/b"]))
    app = create_app(reg, JanusConfig(server=ServerSettings(port=0)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert "a/b" in ids
        assert "stk" in ids


@pytest.mark.asyncio
@respx.mock
async def test_rtk_compresses_tool_result_before_provider():
    """RTK should compress tool_result content before it reaches the provider."""
    from janus.config.schema import TokenSaverConfig, TokenSaverSettings

    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="t",
            prefix="t",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="k",
            models=["m"],
        )
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0),
        token_savers=TokenSaverConfig(rtk=TokenSaverSettings(enabled=True)),
    )
    app = create_app(reg, cfg)

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
    from janus.storage.database import init_db
    from janus.storage.usage import get_usage_stats

    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="t",
            prefix="t",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="k",
            models=["m"],
        )
    )
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    app = create_app(reg, cfg)
    await init_db(app.state.db_path)

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
