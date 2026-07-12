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
    pricing = registry.get(model)
    if pricing is None:
        logger.debug("No pricing for model %s; recording cost as $0.00", model)
        return 0.0
    return (
        usage.input_tokens / 1_000_000 * pricing.input_per_mtok
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
