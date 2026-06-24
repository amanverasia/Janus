from __future__ import annotations

from janus.canonical.models import Usage

from .registry import PricingRegistry


def compute_cost(usage: Usage, model: str, registry: PricingRegistry) -> float:
    pricing = registry.get(model)
    if pricing is None:
        return 0.0
    return (
        usage.input_tokens / 1_000_000 * pricing.input_per_mtok
        + usage.output_tokens / 1_000_000 * pricing.output_per_mtok
        + usage.cache_creation_input_tokens / 1_000_000 * pricing.cache_creation_per_mtok
        + usage.cache_read_input_tokens / 1_000_000 * pricing.cache_read_per_mtok
    )
