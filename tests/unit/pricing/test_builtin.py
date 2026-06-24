from janus.pricing.builtin import BUILTIN_PRICING
from janus.pricing.models import ModelPricing


def test_builtin_has_popular_models():
    expected = [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "gpt-4o",
        "gpt-4o-mini",
        "gemini-2.0-flash",
    ]
    for model in expected:
        assert model in BUILTIN_PRICING, f"Missing builtin pricing for {model}"


def test_all_pricing_rates_non_negative():
    for model, pricing in BUILTIN_PRICING.items():
        assert pricing.input_per_mtok >= 0, f"{model}: negative input rate"
        assert pricing.output_per_mtok >= 0, f"{model}: negative output rate"
        assert pricing.cache_creation_per_mtok >= 0, f"{model}: negative cache_creation rate"
        assert pricing.cache_read_per_mtok >= 0, f"{model}: negative cache_read rate"


def test_pricing_is_model_pricing_instances():
    for model, pricing in BUILTIN_PRICING.items():
        assert isinstance(pricing, ModelPricing), f"{model}: not a ModelPricing instance"
