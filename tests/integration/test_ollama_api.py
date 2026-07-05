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
        combos=[ComboConfig(name="stack", models=["test/test-m1"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _mock_upstream() -> None:
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


@pytest.mark.asyncio
@respx.mock
async def test_ollama_chat_nonstream(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }
        r = await client.post("/api/chat", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["done"] is True
        assert data["message"]["content"] == "Hello!"
        assert data["prompt_eval_count"] == 5
        assert data["eval_count"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_ollama_chat_stream_ndjson(app):
    import json

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
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        async with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
    lines = [json.loads(line) for line in body.decode().strip().split("\n")]
    assert lines[0]["message"]["content"] == "Hello"
    assert lines[0]["done"] is False
    assert lines[-1]["done"] is True
    assert lines[-1]["done_reason"] == "stop"


@pytest.mark.asyncio
async def test_ollama_tags_lists_models_and_combos(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/tags")
        assert r.status_code == 200
        names = [m["name"] for m in r.json()["models"]]
        assert "test/test-m1" in names
        assert "stack" in names


@pytest.mark.asyncio
async def test_ollama_version(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/version")
        assert r.status_code == 200
        assert "version" in r.json()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_tool_round_trip_to_openai_upstream(app):
    import json

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
            "stream": False,
            "messages": [
                {"role": "user", "content": "list files"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "ls", "arguments": {"path": "."}}}],
                },
                {"role": "tool", "content": "a.txt"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "ls", "parameters": {"type": "object"}},
                }
            ],
        }
        r = await client.post("/api/chat", json=payload)
        assert r.status_code == 200

    upstream = json.loads(route.calls.last.request.content)
    assistant_msg = next(m for m in upstream["messages"] if m.get("tool_calls"))
    tool_msg = next(m for m in upstream["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == assistant_msg["tool_calls"][0]["id"]
    assert tool_msg["content"] == "a.txt"
