from janus.catalog import gateway_entries
from janus.providers.drivers import get_driver, native_format_for, supported_api_types


def test_driver_registry_covers_every_gateway_api_type() -> None:
    api_types = {str(entry["api_type"]) for entry in gateway_entries().values()}
    assert api_types <= supported_api_types()
    for api_type in api_types:
        assert get_driver(api_type) is not None


def test_driver_aliases_share_native_formats() -> None:
    assert native_format_for("gemini_cli") == native_format_for("antigravity") == "gemini"
    assert native_format_for("gemini-cli") == "gemini"
    assert native_format_for("claude") == native_format_for("claude_oauth") == "anthropic"


def test_unknown_compat_driver_keeps_legacy_native_fallback() -> None:
    assert native_format_for("vendor_compat") == "vendor"
