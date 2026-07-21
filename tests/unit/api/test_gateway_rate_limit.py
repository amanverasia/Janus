from janus.api.rate_limit import GatewayRateLimiter


def test_gateway_rate_limiter_allows_then_blocks() -> None:
    limiter = GatewayRateLimiter(window_seconds=60)

    first = limiter.check("key:1", 2, now=100)
    second = limiter.check("key:1", 2, now=101)
    blocked = limiter.check("key:1", 2, now=102)

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert blocked.allowed is False
    assert blocked.retry_after == 58


def test_gateway_rate_limiter_isolates_identities() -> None:
    limiter = GatewayRateLimiter(window_seconds=60)

    assert limiter.check("key:1", 1, now=100).allowed is True
    assert limiter.check("key:1", 1, now=101).allowed is False
    assert limiter.check("key:2", 1, now=101).allowed is True


def test_gateway_rate_limiter_resets_window() -> None:
    limiter = GatewayRateLimiter(window_seconds=60)

    assert limiter.check("ip:a", 1, now=100).allowed is True
    assert limiter.check("ip:a", 1, now=159).allowed is False
    assert limiter.check("ip:a", 1, now=160).allowed is True


def test_gateway_rate_limiter_disabled() -> None:
    limiter = GatewayRateLimiter()

    for _ in range(10):
        assert limiter.check("key:1", 0).allowed is True
    assert limiter._requests == {}


def test_gateway_rate_limiter_prunes_empty_buckets() -> None:
    limiter = GatewayRateLimiter(window_seconds=60)
    limiter.check("old", 5, now=100)
    limiter.check("active", 5, now=170)

    limiter.prune(now=170)

    assert "old" not in limiter._requests
    assert "active" in limiter._requests


def test_gateway_rate_limiter_prunes_periodically_during_checks() -> None:
    limiter = GatewayRateLimiter(window_seconds=60, prune_interval=2)

    limiter.check("old", 5, now=100)
    limiter.check("active", 5, now=170)

    assert "old" not in limiter._requests
    assert "active" in limiter._requests
