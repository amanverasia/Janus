import httpx
import pytest
import respx

from janus.providers.anthropic import AnthropicProvider
from janus.providers.gemini import GeminiProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_nonstream():
    respx.post("https://test.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call({"model": "m1", "messages": []}, stream=False)
    assert result.status_code == 200
    assert result.json_data is not None
    assert result.json_data["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_provider():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"type": "message", "content": [{"type": "text", "text": "hi"}]},
        )
    )
    provider = AnthropicProvider(api_key="sk-ant-test")
    result = await provider.call({"model": "c", "messages": [], "max_tokens": 100}, stream=False)
    assert result.json_data["content"][0]["text"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider():
    respx.post(url__regex=r".*generativelanguage\.googleapis\.com.*").mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        )
    )
    provider = GeminiProvider(api_key="test-key")
    result = await provider.call({"model": "gemini-2.0-flash", "contents": []}, stream=False)
    assert result.json_data["candidates"][0]["content"]["parts"][0]["text"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_nonjson_error_body():
    respx.post("https://test.com/v1/chat/completions").mock(
        return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call({"model": "m1", "messages": []}, stream=False)
    assert result.status_code == 502
    assert result.json_data == {"error": "<html>Bad Gateway</html>"}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_nonjson_error_body():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(529, text="overloaded")
    )
    provider = AnthropicProvider(api_key="sk-ant-test")
    result = await provider.call({"model": "c", "messages": [], "max_tokens": 100}, stream=False)
    assert result.status_code == 529
    assert result.json_data == {"error": "overloaded"}


@pytest.mark.asyncio
@respx.mock
async def test_gemini_nonjson_error_body():
    respx.post(url__regex=r".*generativelanguage\.googleapis\.com.*").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    provider = GeminiProvider(api_key="test-key")
    result = await provider.call({"model": "gemini-2.0-flash", "contents": []}, stream=False)
    assert result.status_code == 503
    assert result.json_data == {"error": "Service Unavailable"}


@pytest.mark.asyncio
@respx.mock
async def test_opencode_free_provider():
    respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenCodeFreeProvider()
    result = await provider.call({"model": "test", "messages": []}, stream=False)
    assert result.json_data is not None


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_close():
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_reuses_client():
    call_count = 0
    original_init = httpx.AsyncClient.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        original_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = counting_init
    try:
        provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
        respx.post("https://test.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
        )
        await provider.call({"model": "m1", "messages": []}, stream=False)
        await provider.call({"model": "m1", "messages": []}, stream=False)
        assert call_count == 1, f"Expected 1 client init, got {call_count}"
        await provider.close()
    finally:
        httpx.AsyncClient.__init__ = original_init


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_default_headers():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenAICompatProvider(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or",
        default_headers={"HTTP-Referer": "https://janus.local", "X-Title": "Janus"},
    )
    await provider.call({"model": "m", "messages": []}, stream=False)
    req = route.calls.last.request
    assert req.headers.get("http-referer") == "https://janus.local"
    assert req.headers.get("x-title") == "Janus"
    assert req.headers.get("authorization") == "Bearer sk-or"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_responses_payload_hits_responses_endpoint():
    """A Responses-shaped payload (``input``) posts to /responses, not /chat/completions."""
    responses_route = respx.post("https://test.com/v1/responses").mock(
        return_value=httpx.Response(200, json={"id": "resp", "object": "response"})
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call(
        {"model": "gpt-6-astra", "input": [{"type": "message", "role": "user", "content": "hi"}]},
        stream=False,
    )
    assert result.status_code == 200
    assert responses_route.called
    assert result.json_data == {"id": "resp", "object": "response"}
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_chat_payload_hits_chat_endpoint():
    """A Chat-shaped payload (``messages``) keeps posting to /chat/completions."""
    chat_route = respx.post("https://test.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call({"model": "m", "messages": []}, stream=False)
    assert result.status_code == 200
    assert chat_route.called
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_responses_stream_hits_responses_endpoint():
    sse = (
        'data: {"type":"response.created","response":{"model":"gpt-6-astra"}}\n\n'
        'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    )
    responses_route = respx.post("https://test.com/v1/responses").mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call(
        {"model": "gpt-6-astra", "input": "hi", "stream": True}, stream=True
    )
    assert result.status_code == 200
    assert responses_route.called
    assert result.lines is not None
    lines = [line async for line in result.lines]
    assert any("response.created" in ln for ln in lines)
    await provider.close()
