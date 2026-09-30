from janus.inventory.rate_limit import SubmitRateLimiter


def test_submit_rate_limiter_allows_within_limit():
    limiter = SubmitRateLimiter(limit=5, window_seconds=60)
    assert limiter.allow("client-a", 3) is True
    assert limiter.allow("client-a", 2) is True


def test_submit_rate_limiter_blocks_over_limit():
    limiter = SubmitRateLimiter(limit=3, window_seconds=60)
    assert limiter.allow("client-b", 2) is True
    assert limiter.allow("client-b", 2) is False


def test_submit_rate_limiter_prunes_expired_clients(monkeypatch):
    monkeypatch.setattr("janus.inventory.rate_limit.time.monotonic", lambda: 10.0)
    limiter = SubmitRateLimiter(limit=2, window_seconds=1)
    assert limiter.allow("old-client")
    monkeypatch.setattr("janus.inventory.rate_limit.time.monotonic", lambda: 12.0)
    assert limiter.allow("new-client")
    assert set(limiter._entries) == {"new-client"}


def test_submit_rate_limiter_rejects_oversize_first_batch():
    limiter = SubmitRateLimiter(limit=2)
    assert not limiter.allow("new-client", 3)
