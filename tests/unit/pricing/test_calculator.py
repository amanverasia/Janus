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


def _cache_registry() -> PricingRegistry:
    return PricingRegistry(
        {
            "test-model": {
                "input_per_mtok": 3.0,
                "output_per_mtok": 15.0,
                "cache_creation_per_mtok": 3.75,
                "cache_read_per_mtok": 0.3,
            }
        }
    )


# Cache accounting invariant: ``Usage.input_tokens`` is the *total* prompt
# input; ``cache_creation_input_tokens`` and ``cache_read_input_tokens`` are
# subsets of it. Only the uncached remainder is billed at the full input rate:
#     uncached = max(input - cache_creation - cache_read, 0)


def test_uncached_only_billed_at_input_rate():
    reg = _cache_registry()
    usage = Usage(input_tokens=1_000_000)
    assert abs(compute_cost(usage, "test-model", reg) - 3.0) < 1e-9


def test_cache_read_is_subset_of_input_not_added():
    reg = _cache_registry()
    usage = Usage(input_tokens=1_000_000, cache_read_input_tokens=200_000)
    uncached = 800_000
    expected = uncached / 1_000_000 * 3.0 + 200_000 / 1_000_000 * 0.3
    assert abs(compute_cost(usage, "test-model", reg) - expected) < 1e-9
    assert expected < 3.0, "cached tokens must not be billed at the full input rate"


def test_cache_creation_is_subset_of_input_not_added():
    reg = _cache_registry()
    usage = Usage(input_tokens=1_000_000, cache_creation_input_tokens=400_000)
    uncached = 600_000
    expected = uncached / 1_000_000 * 3.0 + 400_000 / 1_000_000 * 3.75
    assert abs(compute_cost(usage, "test-model", reg) - expected) < 1e-9


def test_mixed_cache_creation_read_and_output():
    reg = _cache_registry()
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_creation_input_tokens=300_000,
        cache_read_input_tokens=200_000,
    )
    uncached = 500_000
    expected = (
        uncached / 1_000_000 * 3.0
        + 500_000 / 1_000_000 * 15.0
        + 300_000 / 1_000_000 * 3.75
        + 200_000 / 1_000_000 * 0.3
    )
    assert abs(compute_cost(usage, "test-model", reg) - expected) < 1e-9


def test_cache_exceeds_input_clamps_uncached_to_zero():
    """Malformed/inconsistent usage where cache subsets exceed reported total
    input must never produce a negative uncached bill."""
    reg = _cache_registry()
    usage = Usage(
        input_tokens=100_000,
        cache_creation_input_tokens=200_000,
        cache_read_input_tokens=50_000,
    )
    expected = 200_000 / 1_000_000 * 3.75 + 50_000 / 1_000_000 * 0.3
    cost = compute_cost(usage, "test-model", reg)
    assert cost >= 0
    assert abs(cost - expected) < 1e-9


def test_zero_cache_fields_behaves_like_plain_input():
    reg = _cache_registry()
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    explicit_zero = Usage(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    assert (
        abs(compute_cost(usage, "test-model", reg) - compute_cost(explicit_zero, "test-model", reg))
        < 1e-9
    )


# --- subscription/OAuth providers: $0 marginal cost ------------------------


def _priced_registry(model: str = "gpt-4o") -> PricingRegistry:
    return PricingRegistry(
        {
            model: {
                "input_per_mtok": 2.5,
                "output_per_mtok": 10.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 1.25,
            }
        }
    )


def test_is_subscription_api_type_true_for_all_subscription_types():
    from janus.pricing.calculator import SUBSCRIPTION_API_TYPES, is_subscription_api_type

    for api_type in (
        "github_copilot",
        "kiro",
        "cursor",
        "codex",
        "claude_oauth",
        "antigravity",
        "mimo_free",
        "opencode_free",
    ):
        assert api_type in SUBSCRIPTION_API_TYPES
        assert is_subscription_api_type(api_type)


def test_is_subscription_api_type_false_for_api_key_types():
    from janus.pricing.calculator import is_subscription_api_type

    for api_type in ("openai_compat", "anthropic", "gemini", ""):
        assert not is_subscription_api_type(api_type)


def test_attempt_cost_zero_for_subscription_provider_even_when_priced():
    from janus.config.schema import ProviderConfig
    from janus.pricing.calculator import attempt_cost
    from janus.providers.registry import ResolvedTarget

    target = ResolvedTarget(
        prefix="copilot",
        model="gpt-4o",
        provider_config=ProviderConfig(
            id="github_copilot",
            prefix="copilot",
            api_type="github_copilot",
            base_url="https://api.githubcopilot.com",
        ),
        native_format="github_copilot",
        account_id="github_copilot-0",
    )
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    reg = _priced_registry()
    # Sanity: the registry *would* price the bare model id.
    assert compute_cost(usage, "gpt-4o", reg) > 0
    assert attempt_cost(usage, target, reg) == 0.0


def test_attempt_cost_delegates_to_compute_cost_for_api_key_provider():
    from janus.config.schema import ProviderConfig
    from janus.pricing.calculator import attempt_cost
    from janus.providers.registry import ResolvedTarget

    target = ResolvedTarget(
        prefix="oai",
        model="gpt-4o",
        provider_config=ProviderConfig(
            id="oai",
            prefix="oai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
        ),
        native_format="openai",
        account_id="oai-0",
    )
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    reg = _priced_registry()
    assert attempt_cost(usage, target, reg) == compute_cost(usage, "gpt-4o", reg)
