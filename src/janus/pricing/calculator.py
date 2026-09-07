from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from janus.canonical.models import Usage

from .registry import PricingRegistry

if TYPE_CHECKING:
    from janus.providers.registry import ResolvedTarget

logger = logging.getLogger(__name__)

# Providers whose access is paid via a subscription or OAuth login rather than
# per-token billing. Their model ids are stored with the routing prefix
# stripped (e.g. a Copilot request records bare "gpt-4o"), which would
# otherwise exact-match the synced pricing catalog and fabricate spend that
# never happened. Marginal cost for these api_types is always $0.
SUBSCRIPTION_API_TYPES = frozenset(
    {
        "github_copilot",
        "kiro",
        "cursor",
        "codex",
        "claude_oauth",
        "claude",
        "antigravity",
        "gemini_cli",
        "gemini-cli",
        "mimo_free",
        "opencode_free",
    }
)


def is_subscription_api_type(api_type: str) -> bool:
    """True when the api_type is billed by subscription/OAuth, not per token."""
    return api_type in SUBSCRIPTION_API_TYPES


def compute_cost(usage: Usage, model: str, registry: PricingRegistry) -> float:
    """Compute the USD cost of a request from its canonical usage.

    Invariant: ``Usage.input_tokens`` is the *total* prompt input, and
    ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` are subsets of
    it (the cache-related portions). Each adapter normalizes its upstream wire
    format to this invariant — e.g. the Anthropic adapter adds its disjoint
    ``input_tokens`` and cache fields into the total, while OpenAI/Gemini
    report total input with cached tokens as a subset.

    Cached tokens are therefore billed once at their own (cheaper) rate, never
    at the full input rate on top of the cache rate. The uncached remainder is
    clamped at zero so inconsistent payloads (cache subsets exceeding the
    reported total) can never produce a negative bill.
    """
    pricing = registry.get(model)
    if pricing is None:
        logger.debug("No pricing for model %s; recording cost as $0.00", model)
        return 0.0
    uncached_input = max(
        usage.input_tokens - usage.cache_creation_input_tokens - usage.cache_read_input_tokens,
        0,
    )
    return (
        uncached_input / 1_000_000 * pricing.input_per_mtok
        + usage.output_tokens / 1_000_000 * pricing.output_per_mtok
        + usage.cache_creation_input_tokens / 1_000_000 * pricing.cache_creation_per_mtok
        + usage.cache_read_input_tokens / 1_000_000 * pricing.cache_read_per_mtok
    )


def attempt_cost(usage: Usage, target: ResolvedTarget, registry: PricingRegistry) -> float:
    """Cost of one routed attempt: $0 for subscription/OAuth providers, else
    ``compute_cost`` against the target's (prefix-stripped) model id.

    All live usage-recording call sites must go through this wrapper so the
    subscription gate cannot be forgotten at a future call site.
    """
    if is_subscription_api_type(target.provider_config.api_type):
        return 0.0
    return compute_cost(usage, target.model, registry)
