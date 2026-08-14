import json
import zlib

import httpx
import pytest
import respx

from janus.app import _build_provider
from janus.config.schema import ProviderConfig
from janus.providers.antigravity import AntigravityProvider
from janus.providers.claude_oauth import ClaudeOAuthProvider
from janus.providers.codex import CodexProvider
from janus.providers.cursor import CursorProvider
from janus.providers.kiro import KiroProvider


def test_build_provider_specialized_types():
    for api_type, cls in [
        ("codex", CodexProvider),
        ("kiro", KiroProvider),
        ("cursor", CursorProvider),
        ("antigravity", AntigravityProvider),
        ("gemini-cli", AntigravityProvider),
        ("claude_oauth", ClaudeOAuthProvider),
    ]:
        p = _build_provider(
            ProviderConfig(
                id=api_type,
                prefix=api_type.replace("-", "_"),
                api_type=api_type,
                base_url="https://example.test",
                api_key="tok",
                models=["m1"],
            )
        )
        assert isinstance(p, cls)


def test_kiro_endpoint_order_and_headers_match_9router():
    social = KiroProvider(
        api_key='{"accessToken":"tok","authMethod":"google"}',
        base_url="https://codewhisperer.us-east-1.amazonaws.com",
    )
    assert "kiro.dev" in social._ordered_bases()[0]
    assert "X-Amz-Target" not in social._headers(
        "https://runtime.us-east-1.kiro.dev/generateAssistantResponse"
    )
    assert (
        social._headers("https://codewhisperer.us-east-1.amazonaws.com/generateAssistantResponse")[
            "X-Amz-Target"
        ]
        == "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"
    )

    api_key = KiroProvider(
        api_key='{"accessToken":"tok","authMethod":"api_key"}',
        base_url="https://runtime.us-east-1.kiro.dev",
    )
    assert api_key._ordered_bases()[0].startswith("https://q.")
    assert api_key._headers(api_key._ordered_bases()[0])["tokentype"] == "API_KEY"

    idc = KiroProvider(
        api_key=('{"accessToken":"tok","authMethod":"idc","extra":{"region":"eu-west-1"}}'),
        base_url="https://runtime.us-east-1.kiro.dev",
    )
    assert "eu-west-1.amazonaws.com" in idc._ordered_bases()[0]


def _eventstream_frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode()
    total = 16 + len(body)
    prelude = total.to_bytes(4, "big") + (0).to_bytes(4, "big")
    prelude_crc = zlib.crc32(prelude).to_bytes(4, "big")
    without_crc = prelude + prelude_crc + body
    return without_crc + zlib.crc32(without_crc).to_bytes(4, "big")


@pytest.mark.asyncio
@respx.mock
async def test_kiro_native_stream_separates_each_openai_sse_event():
    upstream = _eventstream_frame({"content": "hello"}) + _eventstream_frame({"content": " world"})
    respx.post("https://runtime.us-east-1.kiro.dev/generateAssistantResponse").mock(
        return_value=httpx.Response(
            200,
            content=upstream,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )
    )
    provider = KiroProvider(
        api_key='{"accessToken":"tok","authMethod":"google"}',
        base_url="https://runtime.us-east-1.kiro.dev",
    )
    result = await provider.call(
        {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hi"}]},
        stream=True,
    )
    assert result.lines is not None
    lines = [line async for line in result.lines]
    assert len(lines) == 4
    assert lines[1] == lines[3] == ""

    # Model the OpenAI SDK SSEDecoder: data lines within one event are joined
    # with a newline and parsed only when the blank separator arrives.
    events: list[dict[str, object]] = []
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            events.append(json.loads("\n".join(data_lines)))
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    assert [event["choices"][0]["delta"]["content"] for event in events] == [
        "hello",
        " world",
    ]
    await provider.close()


def test_kiro_payload_uses_last_user_as_current_and_preserves_model():
    p = KiroProvider(
        api_key='{"accessToken":"tok","extra":{"profileArn":"arn:test"}}',
        base_url="https://runtime.us-east-1.kiro.dev",
    )
    body = p._to_kiro_payload(
        {
            "model": "claude-sonnet-4",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "last"},
            ],
            "max_tokens": 17,
            "temperature": 0.2,
            "top_p": 0.9,
        }
    )
    state = body["conversationState"]
    assert state["currentMessage"]["userInputMessage"]["content"] == "last"
    assert state["currentMessage"]["userInputMessage"]["modelId"] == "claude-sonnet-4"
    assert state["history"] == [
        {"userInputMessage": {"content": "first", "modelId": "claude-sonnet-4"}},
        {"assistantResponseMessage": {"content": "answer"}},
    ]
    assert body["profileArn"] == "arn:test"
    assert body["inferenceConfig"] == {
        "maxTokens": 17,
        "temperature": 0.2,
        "topP": 0.9,
    }


def test_kiro_payload_preserves_pi_system_tools_and_tool_results():
    p = KiroProvider(api_key="tok", base_url="https://runtime.us-east-1.kiro.dev")
    body = p._to_kiro_payload(
        {
            "model": "claude-sonnet-4",
            "messages": [
                {"role": "system", "content": "You are Pi."},
                {"role": "user", "content": "Read a file."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "contents",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        }
    )

    state = body["conversationState"]
    current = state["currentMessage"]["userInputMessage"]
    assert current["content"] == "You are Pi.\n\ncontinue"
    assert current["userInputMessageContext"]["toolResults"] == [
        {
            "toolUseId": "call_1",
            "content": [{"text": "contents"}],
            "status": "success",
        }
    ]
    assert current["userInputMessageContext"]["tools"][0]["toolSpecification"]["name"] == "read"
    assert state["history"][1]["assistantResponseMessage"]["toolUses"] == [
        {"toolUseId": "call_1", "name": "read", "input": {"path": "a.txt"}}
    ]


@pytest.mark.asyncio
@respx.mock
async def test_kiro_bridge_keeps_openai_payload():
    route = respx.post("https://bridge.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    p = KiroProvider(api_key="tok", base_url="https://bridge.test/v1")
    result = await p.call(
        {"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
        stream=False,
    )
    assert result.status_code == 200
    import json

    assert json.loads(route.calls.last.request.content)["messages"][0]["content"] == "hi"
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_codex_posts_responses():
    sse = (
        'data: {"type":"response.created","response":{"id":"r1","output":[]}}\n\n'
        'data: {"type":"response.completed","response":'
        '{"id":"r1","output":[],"status":"completed"}}\n\n'
    )
    route = respx.post("https://example.test/responses").mock(
        return_value=httpx.Response(
            200,
            content=sse.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    p = CodexProvider(api_key="sk", base_url="https://example.test")
    result = await p.call(
        {
            "model": "o3",
            "input": [{"role": "system", "content": "x"}],
            "max_output_tokens": 32000,
        },
        stream=False,
    )
    assert result.status_code == 200
    assert result.json_data is not None
    assert result.json_data["id"] == "r1"
    assert route.called
    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body["stream"] is True
    assert body["input"][0]["role"] == "developer"
    assert body["store"] is False
    assert "max_output_tokens" not in body
    await p.close()


_CODEX_EMPTY_COMPLETED_SSE = (
    'data: {"type":"response.created","response":{"id":"resp_1","output":[]}}\n\n'
    'data: {"type":"response.output_item.done","output_index":0,'
    '"item":{"id":"fc_1","type":"function_call","status":"completed",'
    '"call_id":"call_1","name":"shell","arguments":"{\\"command\\":\\"ls\\"}"}}\n\n'
    'data: {"type":"response.completed","response":'
    '{"id":"resp_1","status":"completed","output":[],'
    '"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}\n\n'
)


@pytest.mark.asyncio
@respx.mock
async def test_codex_backfills_empty_completed_output_nonstream():
    """ChatGPT Codex leaves response.completed.output empty; rebuild from done items."""
    respx.post("https://example.test/responses").mock(
        return_value=httpx.Response(
            200,
            content=_CODEX_EMPTY_COMPLETED_SSE.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    p = CodexProvider(api_key="sk", base_url="https://example.test")
    result = await p.call(
        {"model": "gpt-5.6-terra", "input": "list files"},
        stream=False,
    )
    assert result.status_code == 200
    assert result.json_data is not None
    out = result.json_data.get("output")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["type"] == "function_call"
    assert out[0]["call_id"] == "call_1"
    assert out[0]["name"] == "shell"
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_codex_backfills_empty_completed_output_stream():
    respx.post("https://example.test/responses").mock(
        return_value=httpx.Response(
            200,
            content=_CODEX_EMPTY_COMPLETED_SSE.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    p = CodexProvider(api_key="sk", base_url="https://example.test")
    result = await p.call(
        {"model": "gpt-5.6-terra", "input": "list files"},
        stream=True,
    )
    assert result.lines is not None
    completed_output = None
    async for line in result.lines:
        if not line.strip().startswith("data:"):
            continue
        data = line.strip()[5:].strip()
        if not data or data == "[DONE]":
            continue
        import json

        event = json.loads(data)
        if event.get("type") == "response.completed":
            completed_output = (event.get("response") or {}).get("output")
    assert isinstance(completed_output, list)
    assert len(completed_output) == 1
    assert completed_output[0]["type"] == "function_call"
    assert completed_output[0]["name"] == "shell"
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_antigravity_strips_thinking_root():
    route = respx.post("https://example.test/v1internal:generateContent").mock(
        return_value=httpx.Response(200, json={"candidates": []})
    )
    p = AntigravityProvider(api_key="tok", base_url="https://example.test")
    result = await p.call(
        {
            "model": "gemini-2.0-flash",
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        stream=False,
    )
    assert result.status_code == 200
    import json

    body = json.loads(route.calls.last.request.content)
    assert "thinking" not in body
    assert "reasoning_effort" not in body
    assert "request" in body
    assert body["model"] == "gemini-2.0-flash"
    assert body["requestType"] == "agent"
    assert body["requestId"].startswith("agent/")
    assert body["requestId"].count("/") == 4
    assert body["project"] is None
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_codex_normalizes_openai_tools():
    route = respx.post("https://example.test/responses").mock(
        return_value=httpx.Response(200, json={"id": "r1", "output": []})
    )
    p = CodexProvider(api_key="sk", base_url="https://example.test")
    result = await p.call(
        {
            "model": "o3",
            "input": [{"type": "message", "role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "run",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        },
        stream=False,
    )
    assert result.status_code == 200
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "bash"
    assert "function" not in body["tools"][0]
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_claude_oauth_posts_messages():
    route = respx.post("https://example.test/v1/messages?beta=true").mock(
        return_value=httpx.Response(200, json={"id": "m1", "content": []})
    )
    p = ClaudeOAuthProvider(api_key="oauth-token", base_url="https://example.test")
    result = await p.call(
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
        stream=False,
    )
    assert result.status_code == 200
    assert route.called
    assert "Bearer oauth-token" in route.calls.last.request.headers["Authorization"]
    await p.close()
