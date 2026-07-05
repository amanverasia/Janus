import json
import time

import httpx
import pytest
import respx

from janus.providers.github_copilot import (
    GitHubCopilotProvider,
    poll_device_flow,
    start_device_flow,
)

TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
CHAT_URL = "https://api.githubcopilot.com/chat/completions"
MODELS_URL = "https://api.githubcopilot.com/models"


def _mock_token_exchange(expires_in: float = 1800.0) -> respx.Route:
    return respx.get(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"token": "copilot-session-token", "expires_at": time.time() + expires_in},
        )
    )


def _mock_chat() -> respx.Route:
    return respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )


# ---- device flow ----


@respx.mock
async def test_start_device_flow():
    respx.post("https://github.com/login/device/code").mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dc123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )
    )
    data = await start_device_flow()
    assert data["device_code"] == "dc123"
    assert data["user_code"] == "ABCD-1234"
    assert data["interval"] == 5


@respx.mock
async def test_poll_device_flow_pending_then_success():
    route = respx.post("https://github.com/login/oauth/access_token")
    route.side_effect = [
        httpx.Response(200, json={"error": "authorization_pending"}),
        httpx.Response(200, json={"access_token": "gho_token", "token_type": "bearer"}),
    ]
    first = await poll_device_flow("dc123")
    assert first["status"] == "pending"
    second = await poll_device_flow("dc123")
    assert second == {"status": "success", "access_token": "gho_token"}


@respx.mock
async def test_poll_device_flow_denied():
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"error": "access_denied"})
    )
    result = await poll_device_flow("dc123")
    assert result["status"] == "error"
    assert result["error"] == "access_denied"


# ---- provider executor ----


@respx.mock
async def test_call_exchanges_token_and_sends_headers():
    exchange = _mock_token_exchange()
    chat = _mock_chat()
    provider = GitHubCopilotProvider(oauth_token="gho_token")
    result = await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.close()

    assert result.status_code == 200
    assert result.json_data is not None
    assert exchange.calls.last.request.headers["Authorization"] == "token gho_token"
    chat_req = chat.calls.last.request
    assert chat_req.headers["Authorization"] == "Bearer copilot-session-token"
    assert chat_req.headers["Copilot-Integration-Id"] == "vscode-chat"
    assert "Editor-Version" in chat_req.headers


@respx.mock
async def test_session_token_is_cached():
    exchange = _mock_token_exchange()
    _mock_chat()
    provider = GitHubCopilotProvider(oauth_token="gho_token")
    await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.close()
    assert exchange.call_count == 1


@respx.mock
async def test_expired_session_token_is_refreshed():
    exchange = _mock_token_exchange(expires_in=30.0)
    _mock_chat()
    provider = GitHubCopilotProvider(oauth_token="gho_token")
    await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.close()
    assert exchange.call_count == 2


@respx.mock
async def test_exchange_failure_surfaces_status():
    respx.get(TOKEN_URL).mock(return_value=httpx.Response(401, json={"message": "bad credentials"}))
    provider = GitHubCopilotProvider(oauth_token="gho_bad")
    result = await provider.call({"model": "gpt-4o", "messages": []}, stream=False)
    await provider.close()
    assert result.status_code == 401
    assert result.json_data == {"message": "bad credentials"}


@respx.mock
async def test_streaming_call():
    _mock_token_exchange()
    sse = 'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\ndata: [DONE]\n\n'
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    provider = GitHubCopilotProvider(oauth_token="gho_token")
    result = await provider.call({"model": "gpt-4o", "messages": []}, stream=True)
    assert result.lines is not None
    lines = [line async for line in result.lines]
    await provider.close()
    assert any("hi" in line for line in lines)
    sent = json.loads(respx.calls.last.request.content)
    assert sent["stream"] is True


@respx.mock
async def test_list_models():
    _mock_token_exchange()
    respx.get(MODELS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "gpt-4o"}, {"id": "claude-sonnet-4"}]}
        )
    )
    provider = GitHubCopilotProvider(oauth_token="gho_token")
    models = await provider.list_models()
    await provider.close()
    assert models == ["gpt-4o", "claude-sonnet-4"]


@respx.mock
async def test_list_models_empty_on_bad_token():
    respx.get(TOKEN_URL).mock(return_value=httpx.Response(401, json={}))
    provider = GitHubCopilotProvider(oauth_token="gho_bad")
    assert await provider.list_models() == []
    await provider.close()


def test_build_provider_github_copilot():
    from janus.app import _build_provider
    from janus.config.schema import ProviderConfig

    config = ProviderConfig(
        id="copilot",
        prefix="copilot",
        api_type="github_copilot",
        base_url="https://api.githubcopilot.com",
        api_key="gho_token",
        models=["gpt-4o"],
    )
    provider = _build_provider(config)
    assert isinstance(provider, GitHubCopilotProvider)


@pytest.mark.parametrize("name", ["github_copilot", "opencode_free"])
def test_resolve_format_maps_to_openai(name):
    from janus.api.routes import FORMATS, _resolve_format

    assert _resolve_format(name) is FORMATS["openai"]
