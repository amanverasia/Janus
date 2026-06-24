from janus.canonical.models import Usage
from janus.pricing.calculator import compute_cost
from janus.pricing.registry import PricingRegistry


def test_basic_cost():
    overrides = {
        "test-model": {
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "cache_creation_per_mtok": 3.75,
            "cache_read_per_mtok": 0.3,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    cost = compute_cost(usage, "test-model", reg)
    assert cost == 3.0 + 7.5


def test_zero_tokens():
    reg = PricingRegistry({})
    usage = Usage()
    cost = compute_cost(usage, "gpt-4o", reg)
    assert cost == 0.0


def test_unknown_model():
    reg = PricingRegistry({})
    usage = Usage(input_tokens=1000, output_tokens=1000)
    cost = compute_cost(usage, "totally-unknown", reg)
    assert cost == 0.0


def test_cache_token_cost():
    overrides = {
        "test-model": {
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "cache_creation_per_mtok": 3.75,
            "cache_read_per_mtok": 0.3,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_input_tokens=500_000,
        cache_read_input_tokens=2_000_000,
    )
    cost = compute_cost(usage, "test-model", reg)
    assert abs(cost - (3.0 + 1.875 + 0.6)) < 0.0001


def test_partial_cache_only():
    overrides = {
        "test-model": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 1.0,
            "cache_creation_per_mtok": 1.0,
            "cache_read_per_mtok": 1.0,
        }
    }
    reg = PricingRegistry(overrides)
    usage = Usage(input_tokens=100, cache_read_input_tokens=200)
    cost = compute_cost(usage, "test-model", reg)
    expected = (100 / 1_000_000 * 1.0) + (200 / 1_000_000 * 1.0)
    assert abs(cost - expected) < 0.0001
