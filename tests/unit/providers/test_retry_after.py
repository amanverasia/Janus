import httpx
import respx

from janus.providers.base import parse_retry_after
from janus.providers.openai_compat import OpenAICompatProvider


def test_parse_retry_after_seconds():
    assert parse_retry_after({"retry-after": "42"}) == 42.0


def test_parse_retry_after_absent():
    assert parse_retry_after({}) is None


def test_parse_retry_after_garbage_returns_none():
    assert parse_retry_after({"retry-after": "not-a-date"}) is None


def test_parse_retry_after_future_http_date():
    # An HTTP-date far in the future yields a large positive delay.
    secs = parse_retry_after({"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"})
    assert secs is not None
    assert secs > 1_000_000


def test_parse_retry_after_past_http_date_clamps_to_zero():
    # A date in the past must not produce a negative cooldown.
    secs = parse_retry_after({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert secs == 0.0


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
