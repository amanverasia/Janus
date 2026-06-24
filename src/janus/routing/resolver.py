from __future__ import annotations

from janus.providers.registry import ProviderRegistry, ResolvedTarget


def resolve(model_str: str, registry: ProviderRegistry) -> ResolvedTarget | None:
    return registry.lookup(model_str)
