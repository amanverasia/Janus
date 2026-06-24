from __future__ import annotations

from janus.providers.registry import ProviderRegistry, ResolvedTarget


class FallbackHandler:
    """Phase 1: single-model resolution, no fallback.
    Phase 2 will add combo sequences and multi-account rotation."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def resolve(self, model: str) -> ResolvedTarget:
        target = self.registry.lookup(model)
        if target is None:
            raise ValueError(f"Unknown model: {model}")
        return target
