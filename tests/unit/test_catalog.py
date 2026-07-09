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

GATEWAY_FIELDS = {
    "id",
    "name",
    "icon",
    "logo",
    "api_type",
    "base_url",
    "prefix",
    "default_models",
    "transports",
    "default_headers",
}


def test_unified_catalog_counts() -> None:
    assert len(PROVIDERS) == 49
    assert len(inventory_entries()) == 38
    assert len(gateway_entries()) == 38


def test_groq_default_model_is_valid() -> None:
    # llama-3.3-70b-instruct does not exist on Groq; the versatile id is correct.
    assert "llama-3.3-70b-versatile" in PROVIDERS["groq"]["gateway"]["default_models"]
    assert "llama-3.3-70b-instruct" not in PROVIDERS["groq"]["gateway"]["default_models"]


def test_cohere_is_routable() -> None:
    assert "gateway" in PROVIDERS["cohere"]
    assert PROVIDERS["cohere"]["gateway"]["prefix"] == "cohere"
    assert PROVIDERS["cohere"]["gateway"]["api_type"] == "openai_compat"


def test_new_9router_providers_present() -> None:
    for pid in (
        "cerebras",
        "hyperbolic",
        "nebius",
        "chutes",
        "venice",
        "vercel-ai-gateway",
        "volcengine-ark",
        "byteplus",
        "codex",
        "kiro",
        "cursor",
        "antigravity",
        "claude_oauth",
        "xiaomi",
        "xiaomi_tokenplan",
        "mimo_free",
    ):
        assert pid in PROVIDERS, pid
        assert "gateway" in PROVIDERS[pid], pid
        if pid not in ("codex", "kiro", "cursor", "antigravity", "claude_oauth", "mimo_free"):
            assert "inventory" in PROVIDERS[pid], pid


def test_inventory_view_derives_from_unified() -> None:
    assert INVENTORY_PROVIDERS == inventory_entries()
    for provider_id, entry in INVENTORY_PROVIDERS.items():
        assert set(entry) == INVENTORY_FIELDS
        assert entry["id"] == provider_id


def test_gateway_view_derives_from_unified() -> None:
    assert CATALOG == gateway_entries()
    assert list(CATALOG) == GATEWAY_ORDER
    for entry in CATALOG.values():
        assert GATEWAY_FIELDS - {"transports", "default_headers"} <= set(entry) | {"id"}
        assert set(entry) | {"id"} <= GATEWAY_FIELDS


def test_id_bridges_are_derived() -> None:
    assert inventory_to_gateway_map() == {"google": "gemini", "dashscope": "qwen"}
    assert prefix_to_inventory_map() == {
        "gemini": "google",
        "qwen": "dashscope",
        "ark": "volcengine-ark",
        "vercel": "vercel-ai-gateway",
        "xmtp": "xiaomi_tokenplan",
    }


def test_gateway_only_entries_have_no_inventory_block() -> None:
    for pid in ("opencode_free", "mimo_free", "claude_oauth", "codex", "kiro", "cursor", "antigravity"):
        assert pid in PROVIDERS
        assert "inventory" not in PROVIDERS[pid]
        assert PROVIDERS[pid]["gateway"]["id"] == pid


def test_shared_entries_agree_on_base_urls_where_expected() -> None:
    for provider_id in ("openai", "openrouter", "groq", "deepseek", "mistral", "xai"):
        entry = PROVIDERS[provider_id]
        assert entry["inventory"]["base_url"] == entry["gateway"]["base_url"]
