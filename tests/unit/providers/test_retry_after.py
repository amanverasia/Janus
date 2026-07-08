import httpx
import respx

from janus.providers.base import parse_retry_after
from janus.providers.openai_compat import OpenAICompatProvider


def test_parse_retry_after_seconds():
    assert parse_retry_after({"retry-after": "42"}) == 42.0


def test_parse_retry_after_absent():
    assert parse_retry_after({}) is None


def test_parse_retry_after_nonnumeric_returns_none():
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}) is None


@respx.mock
async def test_stream_429_sets_retry_after():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"}, json={"e": 1})
    )
    p = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    r = await p.call({"model": "m", "messages": []}, stream=True)
    assert r.status_code == 429
    assert r.retry_after == 30.0
    await p.close()
