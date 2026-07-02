from janus.inventory.rate_limit import SubmitRateLimiter


def test_submit_rate_limiter_allows_within_limit():
    limiter = SubmitRateLimiter(limit=5, window_seconds=60)
    assert limiter.allow("client-a", 3) is True
    assert limiter.allow("client-a", 2) is True


def test_submit_rate_limiter_blocks_over_limit():
    limiter = SubmitRateLimiter(limit=3, window_seconds=60)
    assert limiter.allow("client-b", 2) is True
    assert limiter.allow("client-b", 2) is False
