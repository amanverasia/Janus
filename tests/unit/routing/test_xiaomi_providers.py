from janus.app import _build_provider
from janus.catalog import PROVIDERS, gateway_entries
from janus.config.schema import ProviderConfig
from janus.inventory.url_guard import detect_provider_from_key
from janus.providers.mimo_free import MimoFreeProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.registry import ProviderRegistry, _native_format
from janus.routing.capabilities import get_capabilities_for_model
from janus.routing.provider_provision import routing_catalog_id_for_inventory
from janus.routing.upstream_expand import expand_gateway_provider


def test_xiaomi_variants_in_catalog() -> None:
    assert "xiaomi" in PROVIDERS and "gateway" in PROVIDERS["xiaomi"]
    assert "xiaomi_tokenplan" in PROVIDERS and "gateway" in PROVIDERS["xiaomi_tokenplan"]
    assert "mimo_free" in PROVIDERS and "gateway" in PROVIDERS["mimo_free"]
    gw = gateway_entries()
    assert gw["xiaomi"]["prefix"] == "xiaomi"
    assert gw["xiaomi_tokenplan"]["prefix"] == "xmtp"
    assert gw["mimo_free"]["prefix"] == "mmf"
    assert gw["xiaomi"]["transports"]["anthropic"].endswith("/anthropic/v1")
    assert gw["xiaomi_tokenplan"]["base_url"].startswith("https://token-plan-sgp.")


def test_tp_keys_detect_tokenplan() -> None:
    assert detect_provider_from_key("tp-abc123") == "xiaomi_tokenplan"
    assert routing_catalog_id_for_inventory("xiaomi_tokenplan") == "xiaomi_tokenplan"
    assert routing_catalog_id_for_inventory("xiaomi") == "xiaomi"


def test_build_providers() -> None:
    mimo = _build_provider(
        ProviderConfig(
            id="xiaomi",
            prefix="xiaomi",
            api_type="openai_compat",
            base_url="https://api.xiaomimimo.com/v1",
            api_key="k",
            models=["mimo-v2.5-pro"],
            transports={"anthropic": "https://api.xiaomimimo.com/anthropic/v1"},
        )
    )
    assert isinstance(mimo, OpenAICompatProvider)
    free = _build_provider(
        ProviderConfig(
            id="mimo_free",
            prefix="mmf",
            api_type="mimo_free",
            base_url="",
            models=["mimo-auto"],
        )
    )
    assert isinstance(free, MimoFreeProvider)
    assert _native_format("mimo_free") == "openai"


def test_tokenplan_region_via_custom_base_url() -> None:
    row = {
        "id": "xiaomi_tokenplan",
        "prefix": "xmtp",
        "api_type": "openai_compat",
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "api_key": None,
        "models": '["mimo-v2.5-pro"]',
        "quota_window": None,
        "quota_limit": None,
        "quota_metric": "requests",
        "transports": '{"anthropic": "https://token-plan-sgp.xiaomimimo.com/anthropic/v1"}',
    }
    keys = [
        {
            "id": "k-cn",
            "key_value": "tp-cn",
            "custom_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "rate_limit_rpm": None,
            "rate_limit_rpd": None,
        },
        {
            "id": "k-sgp",
            "key_value": "tp-sgp",
            "custom_base_url": None,
            "rate_limit_rpm": None,
            "rate_limit_rpd": None,
        },
    ]
    configs = expand_gateway_provider(row, keys)
    assert configs[0].base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert configs[1].base_url == "https://token-plan-sgp.xiaomimimo.com/v1"
    reg = ProviderRegistry()
    for pc in configs:
        reg.register(pc)
    targets = reg.lookup("xmtp/mimo-v2.5-pro")
    assert targets is not None and len(targets) == 2


def test_mimo_model_caps() -> None:
    caps = get_capabilities_for_model("xiaomi", "mimo-v2.5-pro")
    assert caps["vision"] is True
    assert caps["reasoning"] is True
