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
    "auth_kind",
    "key_optional",
    "allow_private_network",
    "live_models",
    "default_model",
    "featured",
    "group",
    "capabilities",
    "model_discovery",
}


def test_unified_catalog_counts() -> None:
    assert len(PROVIDERS) == 77
    assert len(inventory_entries()) == 43
    assert len(gateway_entries()) == 69


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
        "ollama",
    ):
        assert pid in PROVIDERS, pid
        assert "gateway" in PROVIDERS[pid], pid
        if pid not in ("cursor", "antigravity", "claude_oauth", "mimo_free"):
            assert "inventory" in PROVIDERS[pid], pid


def test_inventory_view_derives_from_unified() -> None:
    from janus.catalog import inventory_catalog_entries

    assert INVENTORY_PROVIDERS == inventory_catalog_entries()
    for provider_id, entry in INVENTORY_PROVIDERS.items():
        optional = {"model_format", "allow_private_network"}
        assert set(entry) - optional == INVENTORY_FIELDS
        assert set(entry) <= INVENTORY_FIELDS | optional
        assert entry["id"] == provider_id


def test_gateway_view_derives_from_unified() -> None:
    assert CATALOG == gateway_entries()
    assert list(CATALOG) == GATEWAY_ORDER
    for entry in CATALOG.values():
        optional = {"transports", "default_headers", "allow_private_network", "model_discovery"}
        assert GATEWAY_FIELDS - optional <= set(entry) | {"id"}
        assert set(entry) | {"id"} <= GATEWAY_FIELDS


def test_opencodex_style_presets_share_reusable_executor() -> None:
    catalog = gateway_entries()
    for provider_id in (
        "baseten",
        "deepinfra",
        "digitalocean",
        "featherless",
        "huggingface",
        "lm-studio",
        "nscale",
        "nvidia",
        "ollama-local",
        "sambanova",
        "scaleway",
        "siliconflow",
        "vllm",
        "vultr",
    ):
        assert catalog[provider_id]["api_type"] == "openai_compat"
        assert catalog[provider_id]["live_models"] is True


def test_local_presets_are_key_optional_and_private_network_aware() -> None:
    catalog = gateway_entries()
    for provider_id in ("litellm", "lm-studio", "ollama-local", "vllm"):
        assert catalog[provider_id]["auth_kind"] == "local"
        assert catalog[provider_id]["key_optional"] is True
        assert catalog[provider_id]["allow_private_network"] is True


def test_id_bridges_are_derived() -> None:
    assert inventory_to_gateway_map() == {"google": "gemini", "dashscope": "qwen"}
    assert prefix_to_inventory_map() == {
        "gemini": "google",
        "qwen": "dashscope",
        "ark": "volcengine-ark",
        "vercel": "vercel-ai-gateway",
        "xmtp": "xiaomi_tokenplan",
        "minimax-io": "minimax_io",
        "kimi": "kimi_coding",
        "glm": "glm_coding",
    }


def test_gateway_only_entries_have_no_inventory_block() -> None:
    gateway_only = (
        "opencode_free",
        "mimo_free",
        "claude_oauth",
        "cursor",
    )
    for pid in gateway_only:
        assert pid in PROVIDERS
        assert "inventory" not in PROVIDERS[pid]
        assert PROVIDERS[pid]["gateway"]["id"] == pid


def test_ollama_cloud_gateway_and_inventory() -> None:
    entry = PROVIDERS["ollama"]
    assert entry["gateway"]["api_type"] == "openai_compat"
    assert entry["gateway"]["base_url"] == "https://ollama.com/v1"
    assert entry["gateway"]["prefix"] == "ollama"
    assert entry["inventory"]["billing_model"] == "subscription"
    assert entry["inventory"]["base_url"] == "https://ollama.com/v1"


def test_shared_entries_agree_on_base_urls_where_expected() -> None:
    for provider_id in ("openai", "openrouter", "groq", "deepseek", "mistral", "xai"):
        entry = PROVIDERS[provider_id]
        assert entry["inventory"]["base_url"] == entry["gateway"]["base_url"]
