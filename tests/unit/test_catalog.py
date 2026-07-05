from janus.catalog import (
    GATEWAY_ORDER,
    PROVIDERS,
    gateway_entries,
    inventory_entries,
    inventory_to_gateway_map,
    prefix_to_inventory_map,
)
from janus.dashboard.catalog import CATALOG
from janus.inventory.catalog import INVENTORY_PROVIDERS

INVENTORY_FIELDS = {
    "id",
    "name",
    "display_name",
    "base_url",
    "auth_type",
    "auth_header",
    "auth_prefix",
    "key_env_var",
    "models_endpoint",
    "health_check_endpoint",
    "credit_check_endpoint",
    "billing_model",
    "is_direct",
    "routing_note",
}

GATEWAY_FIELDS = {"id", "name", "icon", "logo", "api_type", "base_url", "prefix", "default_models"}


def test_unified_catalog_counts() -> None:
    assert len(PROVIDERS) == 30
    assert len(inventory_entries()) == 29
    assert len(gateway_entries()) == 14


def test_inventory_view_derives_from_unified() -> None:
    assert INVENTORY_PROVIDERS == inventory_entries()
    for provider_id, entry in INVENTORY_PROVIDERS.items():
        assert set(entry) == INVENTORY_FIELDS
        assert entry["id"] == provider_id


def test_gateway_view_derives_from_unified() -> None:
    assert CATALOG == gateway_entries()
    assert list(CATALOG) == GATEWAY_ORDER
    for entry in CATALOG.values():
        assert set(entry) | {"id"} == GATEWAY_FIELDS


def test_id_bridges_are_derived() -> None:
    assert inventory_to_gateway_map() == {"google": "gemini", "dashscope": "qwen"}
    assert prefix_to_inventory_map() == {"gemini": "google", "qwen": "dashscope"}


def test_gateway_only_entries_have_no_inventory_block() -> None:
    assert "opencode_free" in PROVIDERS
    assert "inventory" not in PROVIDERS["opencode_free"]
    assert PROVIDERS["opencode_free"]["gateway"]["id"] == "opencode_free"


def test_shared_entries_agree_on_base_urls_where_expected() -> None:
    for provider_id in ("openai", "openrouter", "groq", "deepseek", "mistral", "xai"):
        entry = PROVIDERS[provider_id]
        assert entry["inventory"]["base_url"] == entry["gateway"]["base_url"]
