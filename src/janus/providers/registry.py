from __future__ import annotations

from dataclasses import dataclass

from janus.config.schema import ComboConfig, ProviderConfig


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str
    account_id: str


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, list[ProviderConfig]] = {}
        self._combos: dict[str, list[str]] = {}

    def register(self, config: ProviderConfig) -> None:
        if config.prefix not in self._providers:
            self._providers[config.prefix] = []
        self._providers[config.prefix].append(config)

    def register_combo(self, combo: ComboConfig) -> None:
        self._combos[combo.name] = combo.models

    def clear_combos(self) -> None:
        self._combos = {}

    def lookup(self, model_str: str) -> list[ResolvedTarget] | None:
        if "/" not in model_str:
            return None
        prefix, rest = model_str.split("/", 1)
        configs = self._providers.get(prefix)
        if not configs:
            return None
        results: list[ResolvedTarget] = []
        for config in configs:
            native = config.api_type.replace("_compat", "")
            results.append(
                ResolvedTarget(
                    prefix=prefix,
                    model=rest,
                    provider_config=config,
                    native_format=native,
                    account_id=config.upstream_key_id or config.id,
                )
            )
        return results

    def lookup_combo(self, name: str) -> list[str] | None:
        return self._combos.get(name)

    @property
    def providers(self) -> dict[str, list[ProviderConfig]]:
        return self._providers

    @property
    def combos(self) -> dict[str, list[str]]:
        return self._combos
