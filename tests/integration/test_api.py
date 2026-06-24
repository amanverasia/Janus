import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry


@pytest.fixture
def registry():
    reg = ProviderRegistry()
    reg.register(ProviderConfig(
        id="test", prefix="test", api_type="openai_compat",
        base_url="https://fake.local/v1", api_key="sk-test", models=["test-m1"],
    ))
    return reg


@pytest.fixture
def config():
    return JanusConfig(server=ServerSettings(port=0, require_api_key=False))


@pytest.fixture
def app(registry, config):
    return create_app(registry, config)


@pytest.mark.asyncio
async def test_models_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert any(m["id"] == "test/test-m1" for m in data["data"])


@pytest.mark.asyncio
async def test_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_nonstream(app):
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
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
        })
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
    """Anthropic format inbound, OpenAI-compat upstream — cross-format translation."""
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
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
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        })
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
    sse_lines = [
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},'
        '"finish_reason":null}]}\n\n',
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},'
        '"finish_reason":null}]}\n\n',
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},'
        '"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    sse_body = "".join(sse_lines)
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "model": "no/such-model",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 400
