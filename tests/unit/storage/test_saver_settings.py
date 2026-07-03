from janus.storage.settings import (
    SAVER_SETTING_DEFAULTS,
    resolve_saver_settings,
    saver_enabled,
)


def test_resolve_saver_settings_fills_missing_defaults():
    resolved = resolve_saver_settings({})
    assert resolved == SAVER_SETTING_DEFAULTS
    assert resolved["saver_rtk_enabled"] == "true"
    assert resolved["saver_caveman_enabled"] == "false"


def test_resolve_saver_settings_preserves_stored_values():
    resolved = resolve_saver_settings(
        {
            "saver_rtk_enabled": "false",
            "saver_caveman_enabled": "true",
        }
    )
    assert resolved["saver_rtk_enabled"] == "false"
    assert resolved["saver_caveman_enabled"] == "true"
    assert resolved["saver_ponytail_enabled"] == "false"


def test_saver_enabled():
    assert saver_enabled({}, "saver_rtk_enabled") is True
    assert saver_enabled({"saver_rtk_enabled": "false"}, "saver_rtk_enabled") is False
    assert saver_enabled({}, "saver_caveman_enabled") is False
