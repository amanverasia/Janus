import httpx
import respx

from janus.providers.anthropic import AnthropicProvider
from janus.providers.openai_compat import OpenAICompatProvider


@respx.mock
async def test_stream_429_surfaces_status():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    provider = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 429
    assert result.lines is None
    await provider.close()


@respx.mock
async def test_stream_200_returns_lines():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        )
    )
    provider = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 200
    assert result.lines is not None
    lines = [ln async for ln in result.lines]
    assert any("[DONE]" in ln for ln in lines)
    await provider.close()


@respx.mock
async def test_anthropic_stream_503_surfaces_status():
    respx.post("https://an.test/v1/messages").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    provider = AnthropicProvider(api_key="k", base_url="https://an.test")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 503
    await provider.close()
