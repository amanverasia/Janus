import httpx

from janus.routing.errors import ErrorType, classify_error, is_fallback_eligible


def test_classify_429():
    assert classify_error(429) == ErrorType.RATE_LIMIT


def test_classify_500():
    assert classify_error(500) == ErrorType.SERVER_ERROR


def test_classify_401():
    assert classify_error(401) == ErrorType.AUTH_ERROR


def test_classify_403():
    assert classify_error(403) == ErrorType.AUTH_ERROR


def test_classify_400_not_eligible():
    assert classify_error(400) == ErrorType.CLIENT_ERROR
    assert not is_fallback_eligible(400)


def test_classify_429_eligible():
    assert is_fallback_eligible(429)


def test_classify_500_eligible():
    assert is_fallback_eligible(500)


def test_classify_200_not_eligible():
    assert not is_fallback_eligible(200)


def test_network_error_eligible():
    assert is_fallback_eligible(httpx.ConnectError("test"))


def test_timeout_eligible():
    assert is_fallback_eligible(httpx.TimeoutException("test"))
