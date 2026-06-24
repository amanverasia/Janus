from __future__ import annotations

from dataclasses import dataclass

from janus.config.schema import ProviderConfig


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str  # "openai" | "anthropic" | "gemini"


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] = {}

    def register(self, config: ProviderConfig) -> None:
        self._providers[config.prefix] = config

    def lookup(self, model_str: str) -> ResolvedTarget | None:
        if "/" not in model_str:
            return None
        prefix, rest = model_str.split("/", 1)
        config = self._providers.get(prefix)
        if config is None:
            return None
        native = config.api_type.replace("_compat", "")
        return ResolvedTarget(
            prefix=prefix, model=rest, provider_config=config, native_format=native
        )

    @property
    def providers(self) -> dict[str, ProviderConfig]:
        return self._providers
