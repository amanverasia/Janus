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
@respx.mock
async def test_responses_nonstream(app):
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
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "input": "hi", "instructions": "be brief"}
        r = await client.post("/v1/responses", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "response"
        assert data["status"] == "completed"
        msg = next(i for i in data["output"] if i["type"] == "message")
        assert msg["content"][0]["text"] == "Hello!"
        assert data["usage"]["input_tokens"] == 5
        assert data["usage"]["output_tokens"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_responses_nonstream_tool_call(app):
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
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "ls", "arguments": '{"path": "."}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "input": "list files",
            "tools": [{"type": "function", "name": "ls", "parameters": {"type": "object"}}],
        }
        r = await client.post("/v1/responses", json=payload)
        assert r.status_code == 200
        data = r.json()
        fc = next(i for i in data["output"] if i["type"] == "function_call")
        assert fc["call_id"] == "call_1"
        assert fc["name"] == "ls"


@pytest.mark.asyncio
@respx.mock
async def test_responses_stream(app):
    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
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
        payload = {"model": "test/test-m1", "input": "hi", "stream": True}
        async with client.stream("POST", "/v1/responses", json=payload) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
        text = body.decode()
        assert "event: response.created" in text
        assert "response.output_text.delta" in text
        assert '"delta":"Hello"' in text
        assert "event: response.completed" in text
        assert "[DONE]" not in text


@pytest.mark.asyncio
@respx.mock
async def test_responses_function_call_round_trip_upstream_payload(app):
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "test-m1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "a.txt is empty"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "input": [
                {"type": "message", "role": "user", "content": "list files"},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "ls",
                    "arguments": '{"path": "."}',
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "a.txt"},
            ],
            "tools": [{"type": "function", "name": "ls", "parameters": {"type": "object"}}],
        }
        r = await client.post("/v1/responses", json=payload)
        assert r.status_code == 200

    import json

    upstream = json.loads(route.calls.last.request.content)
    roles = [m["role"] for m in upstream["messages"]]
    assert "tool" in roles
    tool_msg = next(m for m in upstream["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"] == "a.txt"
    assistant_msg = next(m for m in upstream["messages"] if m.get("tool_calls"))
    assert assistant_msg["tool_calls"][0]["id"] == "call_1"
