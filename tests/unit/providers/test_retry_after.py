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


def test_parse_retry_after_x_ratelimit_reset_after():
    assert parse_retry_after({"x-ratelimit-reset-after": "90"}) == 90.0


def test_parse_retry_after_x_ratelimit_reset_epoch():
    import time

    secs = parse_retry_after({"x-ratelimit-reset": str(time.time() + 120)})
    assert secs is not None
    assert 115 < secs <= 120


def test_parse_retry_after_header_precedence():
    assert parse_retry_after({"retry-after": "5", "x-ratelimit-reset-after": "90"}) == 5.0


# ── Google RetryInfo / message-based reset parsing ─────────────────────────


def test_google_retry_info_details():
    from janus.providers.base import parse_google_retry_info

    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo"},
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "39s",
                },
            ],
        }
    }
    assert parse_google_retry_info(body) == 39.0


def test_google_retry_info_from_message():
    from janus.providers.base import parse_google_retry_info

    body = {"error": {"message": "Resource exhausted. Please retry in 26.833s."}}
    assert parse_google_retry_info(body) == 26.833
    body = {"error": {"message": "Your quota will reset after 1h2m3s."}}
    assert parse_google_retry_info(body) == 3723.0


def test_google_retry_info_rejects_junk():
    from janus.providers.base import parse_google_retry_info

    assert parse_google_retry_info(None) is None
    assert parse_google_retry_info({"error": "nope"}) is None
    assert parse_google_retry_info({"error": {"message": "no delay here"}}) is None
    assert (
        parse_google_retry_info(
            {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo"}]}}
        )
        is None
    )


@respx.mock
async def test_gemini_429_sets_retry_after_from_retry_info():
    from janus.providers.gemini import GeminiProvider

    respx.post("https://up.test/v1beta/models/gemini-2.5-pro:generateContent?key=k").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "39s",
                        }
                    ],
                }
            },
        )
    )
    p = GeminiProvider(api_key="k", base_url="https://up.test")
    r = await p.call({"model": "gemini-2.5-pro"}, stream=False)
    assert r.status_code == 429
    assert r.retry_after == 39.0
    await p.close()


# ── GitHub Copilot monthly premium-request exhaustion ───────────────────────


def test_copilot_monthly_usage_retry_after():
    from janus.providers.github_copilot import _monthly_usage_retry_after

    body = {
        "error": {
            "message": "You've reached your additional usage limit for your plan.",
            "code": "quota_exceeded",
        }
    }
    delay = _monthly_usage_retry_after(402, body)
    assert delay is not None
    assert 0 < delay <= 31.5 * 86_400  # at most ~a month out
    assert _monthly_usage_retry_after(429, body) is None
    assert _monthly_usage_retry_after(402, {"error": "payment required"}) is None
    assert _monthly_usage_retry_after(402, None) is None
