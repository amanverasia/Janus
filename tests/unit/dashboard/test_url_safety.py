from janus.dashboard.routes import _reject_unsafe_url


def test_public_https_url_is_allowed():
    assert _reject_unsafe_url("https://api.openai.com/v1") is None


def test_non_http_scheme_rejected():
    resp = _reject_unsafe_url("file:///etc/passwd")
    assert resp is not None
    assert resp.status_code == 400


def test_loopback_url_rejected():
    resp = _reject_unsafe_url("http://127.0.0.1:8080/v1")
    assert resp is not None
    assert resp.status_code == 400


def test_private_ip_url_rejected():
    resp = _reject_unsafe_url("http://192.168.1.10/v1")
    assert resp is not None
    assert resp.status_code == 400


def test_invalid_url_rejected():
    resp = _reject_unsafe_url("::::not a url")
    assert resp is not None
    assert resp.status_code == 400
