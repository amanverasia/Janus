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
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "hi"}}]}
        )
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
    result = await provider.call(
        {"model": "c", "messages": [], "max_tokens": 100}, stream=False
    )
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
async def test_opencode_free_provider():
    respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "hi"}}]}
        )
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
