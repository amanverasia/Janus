from janus.storage.settings import resolve_request_log_retention


def test_retention_default():
    assert resolve_request_log_retention({}) == 500


def test_retention_clamp():
    assert resolve_request_log_retention({"server_request_log_retention": "10"}) == 50
    assert resolve_request_log_retention({"server_request_log_retention": "99999"}) == 5000
    assert resolve_request_log_retention({"server_request_log_retention": "250"}) == 250
