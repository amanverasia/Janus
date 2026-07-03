from janus.storage.analytics_labels import provider_prefix_from_usage_id


def test_provider_prefix_from_inventory_config_id() -> None:
    assert provider_prefix_from_usage_id("deepseek::uk_abc-123") == "deepseek"


def test_provider_prefix_from_plain_id() -> None:
    assert provider_prefix_from_usage_id("openai") == "openai"


def test_provider_prefix_unknown_when_missing() -> None:
    assert provider_prefix_from_usage_id(None) == "unknown"
