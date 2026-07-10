from janus.pricing.models import ModelPricing
from janus.pricing.registry import PricingRegistry


def test_exact_match():
    overrides: dict[str, dict[str, float]] = {}
    reg = PricingRegistry(overrides)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 2.5
    assert p.output_per_mtok == 10.0


def test_user_override_replaces_builtin():
    overrides = {
        "gpt-4o": {
            "input_per_mtok": 5.0,
            "output_per_mtok": 20.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 2.5,
        }
    }
    reg = PricingRegistry(overrides)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 5.0
    assert p.output_per_mtok == 20.0


def test_user_override_adds_new_model():
    overrides = {
        "my-custom-model": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides)
    p = reg.get("my-custom-model")
    assert p is not None
    assert p.input_per_mtok == 1.0


def test_unknown_model_returns_none():
    reg = PricingRegistry({})
    assert reg.get("does-not-exist") is None


def test_prefix_match_strips_date_suffix():
    reg = PricingRegistry({})
    p = reg.get("claude-sonnet-4-20250514-some-alias")
    assert p is not None
    assert p.input_per_mtok == 3.0


def test_prefix_match_strips_latest():
    reg = PricingRegistry({})
    p = reg.get("gpt-4o-latest")
    assert p is not None
    assert p.output_per_mtok == 10.0


def test_vendor_prefix_match():
    reg = PricingRegistry({})
    p = reg.get("openai/gpt-4o-mini")
    assert p is not None
    assert p.input_per_mtok == 0.15


def test_vendor_prefix_with_suffix_match():
    reg = PricingRegistry({})
    p = reg.get("anthropic/claude-sonnet-4-20250514")
    assert p is not None
    assert p.input_per_mtok == 3.0


def test_vendor_prefix_prefers_exact_prefixed_override():
    overrides = {
        "openai/gpt-4o-mini": {
            "input_per_mtok": 9.9,
            "output_per_mtok": 1.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides)
    p = reg.get("openai/gpt-4o-mini")
    assert p is not None
    assert p.input_per_mtok == 9.9


def test_deepseek_v4_pro_pricing():
    reg = PricingRegistry({})
    p = reg.get("deepseek-v4-pro")
    assert p is not None
    assert p.input_per_mtok == 0.435
    assert p.output_per_mtok == 0.87
    assert p.cache_read_per_mtok == 0.003625


def test_deepseek_v4_flash_pricing():
    reg = PricingRegistry({})
    p = reg.get("deepseek/deepseek-v4-flash")
    assert p is not None
    assert p.input_per_mtok == 0.14
    assert p.output_per_mtok == 0.28


def test_source_of_builtin():
    reg = PricingRegistry({})
    assert reg.source_of("gpt-4o") == "builtin"


def test_source_of_unknown_model_is_none():
    reg = PricingRegistry({})
    assert reg.source_of("does-not-exist") is None


def test_catalog_layers_over_builtin():
    catalog = {
        "gpt-4o": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 1.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
        "catalog-only-model": {
            "input_per_mtok": 3.0,
            "output_per_mtok": 4.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    }
    reg = PricingRegistry({}, catalog)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 1.0
    assert reg.source_of("gpt-4o") == "catalog"

    p2 = reg.get("catalog-only-model")
    assert p2 is not None
    assert p2.input_per_mtok == 3.0
    assert reg.source_of("catalog-only-model") == "catalog"


def test_override_beats_catalog_beats_builtin():
    catalog = {
        "gpt-4o": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 1.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    overrides = {
        "gpt-4o": {
            "input_per_mtok": 99.0,
            "output_per_mtok": 99.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides, catalog)
    p = reg.get("gpt-4o")
    assert p is not None
    assert p.input_per_mtok == 99.0
    assert reg.source_of("gpt-4o") == "override"


def test_catalog_prefix_match_still_works():
    catalog = {
        "some-vendor-model": {
            "input_per_mtok": 5.0,
            "output_per_mtok": 6.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry({}, catalog)
    p = reg.get("some-vendor-model-20250101")
    assert p is not None
    assert p.input_per_mtok == 5.0


def test_none_catalog_behaves_like_no_catalog():
    reg = PricingRegistry({}, None)
    p = reg.get("gpt-4o")
    assert p is not None
    assert reg.source_of("gpt-4o") == "builtin"


def test_get_all_returns_merged_table():
    overrides = {
        "custom": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        }
    }
    reg = PricingRegistry(overrides)
    all_pricing = reg.get_all()
    assert "gpt-4o" in all_pricing
    assert "custom" in all_pricing
    assert isinstance(all_pricing["custom"], ModelPricing)
