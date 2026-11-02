import pytest

from janus.storage.database import init_db
from janus.storage.settings import (
    SERVER_SETTING_DEFAULTS,
    ensure_server_defaults,
    get_setting,
    resolve_dispatch_max_attempts,
    resolve_dispatch_timeout_s,
    set_setting,
    validate_dispatch_max_attempts,
    validate_dispatch_timeout_s,
)


@pytest.mark.parametrize(
    ("key", "resolver", "default", "maximum"),
    [
        ("server_dispatch_timeout_s", resolve_dispatch_timeout_s, 60, 600),
        ("server_dispatch_max_attempts", resolve_dispatch_max_attempts, 20, 100),
    ],
)
def test_dispatch_settings_defaults_and_clamping(key, resolver, default, maximum):
    assert SERVER_SETTING_DEFAULTS[key] == str(default)
    assert resolver({}) == default
    for invalid in ("", "invalid", "NaN", "Infinity", "-inf", "1.5", "null"):
        assert resolver({key: invalid}) == default
    for value in ("-100", "0", "1"):
        assert resolver({key: value}) == 1
    for value in (str(maximum), str(maximum + 1), "100000000000000000000"):
        assert resolver({key: value}) == maximum
    assert resolver({key: "42"}) == 42


@pytest.mark.parametrize(
    ("validator", "maximum"),
    [(validate_dispatch_timeout_s, 600), (validate_dispatch_max_attempts, 100)],
)
def test_dispatch_setting_validation(validator, maximum):
    for valid in ("1", "42", str(maximum)):
        validator(valid)
    for invalid in ("", "invalid", "NaN", "inf", "1.5", "-1", "0", str(maximum + 1)):
        with pytest.raises(ValueError):
            validator(invalid)


async def test_dispatch_defaults_preserve_existing_limits(tmp_path):
    db_path = tmp_path / "settings.db"
    await init_db(db_path)
    await set_setting(db_path, "server_dispatch_timeout_s", "120")

    await ensure_server_defaults(db_path)

    assert await get_setting(db_path, "server_dispatch_timeout_s") == "120"
    assert await get_setting(db_path, "server_dispatch_max_attempts") == "20"
