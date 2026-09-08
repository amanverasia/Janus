"""Issue #149: Responses-only OpenAI models (gpt-6-astra) must be served from
/v1/responses when a chat/completions client sends function tools.

OpenAI rejects ``tools`` + ``reasoning_effort`` on ``/v1/chat/completions`` for
these models, so Janus promotes the per-attempt wire format to
``openai_responses`` and posts the Responses payload to ``{base}/responses``.
"""

import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

BASE = "https://oai.local/v1"


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
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openai",
                prefix="openai",
                api_type="openai_compat",
                base_url=BASE,
                api_key="sk-test",
                models=["gpt-6-astra", "gpt-4o"],
            )
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _responses_tool_call_body() -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "model": "gpt-6-astra",
        "status": "completed",
        "output": [
            {
                "id": "fc_1",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "ls",
                "arguments": '{"path": "."}',
            }
        ],
        "usage": {
            "input_tokens": 5,
            "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 0},
        },
    }


def _sse(*events: dict) -> bytes:
    out = []
    for ev in events:
        out.append("data: " + json.dumps(ev, separators=(",", ":")))
    return ("\n\n".join(out) + "\n\n").encode()


@pytest.mark.asyncio
@respx.mock
async def test_gpt6_astra_chat_tools_routes_to_responses(app):
    """A /chat/completions request with tools hits /responses, not /chat/completions."""
    responses_route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx.Response(200, json=_responses_tool_call_body())
    )
    chat_route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "should not be called"})
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-6-astra",
            "reasoning_effort": "medium",
            "messages": [{"role": "user", "content": "list files"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "ls",
                        "description": "list files",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        data = r.json()

    assert responses_route.called
    assert not chat_route.called
    assert data["object"] == "chat.completion"
    choice = data["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    tc = choice["message"]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "ls"
    assert json.loads(tc["function"]["arguments"]) == {"path": "."}
    assert data["usage"]["prompt_tokens"] == 5
    assert data["usage"]["completion_tokens"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_gpt6_astra_forwards_reasoning_effort_to_responses(app):
    """reasoning_effort from the chat request is translated to the Responses ``reasoning`` block."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_responses_tool_call_body())

    respx.post(f"{BASE}/responses").mock(side_effect=_capture)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-6-astra",
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "think hard"}],
            "tools": [
                {"type": "function", "function": {"name": "ls", "parameters": {"type": "object"}}}
            ],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    assert captured["model"] == "gpt-6-astra"
    assert "input" in captured
    assert captured["reasoning"] == {"effort": "high"}
    assert captured["store"] is False
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
@respx.mock
async def test_gpt6_astra_chat_tools_routes_to_responses_stream(app):
    """Streaming tool-call translation: Responses SSE -> chat.completion.chunk SSE."""
    args = json.dumps({"path": "."})
    sse = _sse(
        {"type": "response.created", "response": {"model": "gpt-6-astra"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "call_1", "name": "ls"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": args[:6],
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": args[6:],
        },
        {"type": "response.output_item.done", "output_index": 0, "item": {}},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [{"type": "function_call"}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        },
    )
    responses_route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    chat_route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "should not be called"})
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-6-astra",
            "stream": True,
            "messages": [{"role": "user", "content": "list files"}],
            "tools": [
                {"type": "function", "function": {"name": "ls", "parameters": {"type": "object"}}}
            ],
        }
        body = b""
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_bytes():
                body += chunk

    assert responses_route.called
    assert not chat_route.called
    text = body.decode()
    assert "chat.completion.chunk" in text
    assert '"name":"ls"' in text
    assert '"tool_calls"' in text
    assert '"finish_reason":"tool_calls"' in text
    assert "data: [DONE]" in text


@pytest.mark.asyncio
@respx.mock
async def test_gpt6_astra_plain_chat_also_routes_to_responses(app):
    """requires_responses promotes the wire format even without tools."""
    responses_route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp_2",
                "object": "response",
                "model": "gpt-6-astra",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_1",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hi there"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )
    )
    chat_route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "should not be called"})
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-6-astra",
            "messages": [{"role": "user", "content": "hello"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    assert responses_route.called
    assert not chat_route.called
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hi there"


@pytest.mark.asyncio
@respx.mock
async def test_non_responses_model_still_uses_chat_completions(app):
    """Regression guard: a normal OpenAI model keeps using /chat/completions."""
    responses_route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx.Response(400, json={"error": "should not be called"})
    )
    chat_route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    assert chat_route.called
    assert not responses_route.called
    assert r.json()["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_gpt6_astra_responses_client_passthrough_uses_responses(app):
    """A native /v1/responses client to gpt-6-astra passes through to /responses."""
    responses_route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx.Response(200, json=_responses_tool_call_body())
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "openai/gpt-6-astra",
            "input": "list files",
            "tools": [{"type": "function", "name": "ls", "parameters": {"type": "object"}}],
        }
        r = await client.post("/v1/responses", json=payload)
        assert r.status_code == 200

    assert responses_route.called
    data = r.json()
    assert data["object"] == "response"
    fc = next(i for i in data["output"] if i["type"] == "function_call")
    assert fc["name"] == "ls"
    assert fc["call_id"] == "call_1"
