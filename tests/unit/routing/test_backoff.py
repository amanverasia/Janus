from janus.routing.errors import RETRY_AFTER_CAP_S, get_cooldown


def test_rate_limit_backoff_escalates():
    assert get_cooldown("rate_limit", 0) == (2.0, 1)
    assert get_cooldown("rate_limit", 1) == (4.0, 2)
    assert get_cooldown("rate_limit", 2) == (8.0, 3)


def test_rate_limit_backoff_caps_at_300s():
    secs, level = get_cooldown("rate_limit", 14)
    assert secs == 300.0
    assert level == 15


def test_rate_limit_backoff_level_caps_at_15():
    secs, level = get_cooldown("rate_limit", 99)
    assert level == 15
    assert secs == 300.0


def test_fixed_cooldowns_no_backoff():
    assert get_cooldown("server_error", 0) == (30.0, 0)
    assert get_cooldown("auth_error", 3) == (300.0, 0)
    assert get_cooldown("network", 5) == (15.0, 0)


def test_unknown_error_default():
    assert get_cooldown("unknown", 0) == (60.0, 0)


def test_retry_after_cap_constant():
    assert RETRY_AFTER_CAP_S == 1800.0
