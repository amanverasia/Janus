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
