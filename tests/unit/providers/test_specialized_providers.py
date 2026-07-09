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


@pytest.mark.asyncio
@respx.mock
async def test_codex_posts_responses():
    route = respx.post("https://example.test/responses").mock(
        return_value=httpx.Response(200, json={"id": "r1", "output": []})
    )
    p = CodexProvider(api_key="sk", base_url="https://example.test")
    result = await p.call(
        {"model": "o3", "input": [{"role": "system", "content": "x"}]},
        stream=False,
    )
    assert result.status_code == 200
    assert route.called
    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body["input"][0]["role"] == "developer"
    assert body["store"] is False
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
