import asyncio

from janus.dashboard.routes import _reject_unsafe_url


def _check(url: str):
    return asyncio.run(_reject_unsafe_url(url))


def test_public_https_url_is_allowed():
    assert _check("https://api.openai.com/v1") is None


def test_non_http_scheme_rejected():
    resp = _check("file:///etc/passwd")
    assert resp is not None
    assert resp.status_code == 400


def test_loopback_url_rejected():
    resp = _check("http://127.0.0.1:8080/v1")
    assert resp is not None
    assert resp.status_code == 400


def test_private_ip_url_rejected():
    resp = _check("http://192.168.1.10/v1")
    assert resp is not None
    assert resp.status_code == 400


def test_invalid_url_rejected():
    resp = _check("::::not a url")
    assert resp is not None
    assert resp.status_code == 400


def test_unresolvable_hostname_is_allowed_and_fails_later_at_connect():
    # DNS failure must not 400 here; the probe surfaces it as a connect error.
    assert _check("https://no-such-host.invalid/v1") is None


def test_dns_resolution_does_not_block_the_event_loop(monkeypatch):
    import socket
    import time

    def slow_getaddrinfo(*args, **kwargs):
        time.sleep(0.6)
        return [(socket.AF_INET, None, None, "", ("192.168.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)

    async def main():
        loop = asyncio.get_running_loop()
        started = loop.time()
        task = asyncio.ensure_future(_reject_unsafe_url("https://guarded.example/v1"))
        progressed_at: float | None = None
        while not task.done():
            await asyncio.sleep(0.05)
            if progressed_at is None:
                progressed_at = loop.time()
        response = await task
        assert progressed_at is not None
        return progressed_at - started, response

    elapsed, response = asyncio.run(main())
    assert elapsed < 0.5
    assert response is not None
    assert response.status_code == 400
