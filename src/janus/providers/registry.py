from __future__ import annotations

import fnmatch
from collections.abc import Collection
from dataclasses import dataclass

from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.drivers import native_format_for


def _native_format(api_type: str) -> str:
    return native_format_for(api_type)


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str
    account_id: str


@dataclass(frozen=True)
class _AccountRouteIndex:
    accepts_any_model: bool
    allowed_models: tuple[str, ...]
    supported_models: frozenset[str]

    def supports(self, model: str) -> bool:
        if self.accepts_any_model:
            return model_allowed(model, self.allowed_models)
        return model in self.supported_models


# Client-facing prefix aliases → registered gateway prefix.
# e.g. "mimo/mimo-v2.5" routes like "xiaomi/mimo-v2.5".
PREFIX_ALIASES: dict[str, str] = {
    "mimo": "xiaomi",
}


def model_allowed(model: str, allowed: Collection[str]) -> bool:
    """True when `model` passes the allowlist.

    An empty allowlist means no restriction (current default behavior).
    Entries may be exact model names or fnmatch globs (e.g. "claude-opus-*").
    """
    if not allowed:
        return True
    return any(model == pattern or fnmatch.fnmatchcase(model, pattern) for pattern in allowed)


def _model_id_matches(model: str, prefix: str, candidate: str) -> bool:
    return candidate == model or candidate == f"{prefix}/{model}"


def _account_supports(config: ProviderConfig, model: str) -> bool:
    if not model_allowed(model, config.allowed_models):
        return False
    if any(
        _model_id_matches(model, config.prefix, candidate) for candidate in config.custom_models
    ):
        return True
    discovered = config.discovered_models
    if discovered is None:
        return True
    return any(_model_id_matches(model, config.prefix, candidate) for candidate in discovered)


def _model_name(prefix: str, model_id: str) -> str:
    namespaced_prefix = f"{prefix}/"
    if model_id.startswith(namespaced_prefix):
        return model_id[len(namespaced_prefix) :]
    return model_id


def _matching_model_names(prefix: str, model_id: str) -> set[str]:
    return {model_id, _model_name(prefix, model_id)}


def _build_account_route_index(config: ProviderConfig) -> _AccountRouteIndex:
    allowed = tuple(config.allowed_models)
    if config.discovered_models is None:
        return _AccountRouteIndex(
            accepts_any_model=True,
            allowed_models=allowed,
            supported_models=frozenset(),
        )
    supported: set[str] = set()
    for candidate in (*config.custom_models, *config.discovered_models):
        supported.update(
            model
            for model in _matching_model_names(config.prefix, candidate)
            if model_allowed(model, config.allowed_models)
        )
    return _AccountRouteIndex(
        accepts_any_model=False,
        allowed_models=allowed,
        supported_models=frozenset(supported),
    )


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, list[ProviderConfig]] = {}
        self._combos: dict[str, list[str]] = {}
        self._prefix_route_models: dict[str, set[str]] = {}
        self._prefix_unrestricted_patterns: dict[str, set[str]] = {}
        self._prefixes_accepting_any_model: set[str] = set()
        self._prefixes_with_defaults: set[str] = set()
        self._prefixes_with_routable_defaults: set[str] = set()
        self._bare_defaults: set[str] = set()
        self._routable_bare_defaults: set[str] = set()
        self._bare_known: set[str] = set()
        self._routable_bare_known: set[str] = set()

    def register(self, config: ProviderConfig) -> None:
        if config.prefix not in self._providers:
            self._providers[config.prefix] = []
        self._providers[config.prefix].append(config)
        account_routes = _build_account_route_index(config)
        if account_routes.accepts_any_model:
            if account_routes.allowed_models:
                self._prefix_unrestricted_patterns.setdefault(config.prefix, set()).update(
                    account_routes.allowed_models
                )
            else:
                self._prefixes_accepting_any_model.add(config.prefix)
        else:
            self._prefix_route_models.setdefault(config.prefix, set()).update(
                account_routes.supported_models
            )
        if config.default_model is not None:
            self._prefixes_with_defaults.add(config.prefix)
            resolved_default = _model_name(config.prefix, config.default_model)
            if account_routes.supports(resolved_default):
                self._prefixes_with_routable_defaults.add(config.prefix)
            self._index_bare_model(
                self._bare_defaults,
                self._routable_bare_defaults,
                config.default_model,
                config,
                account_routes,
            )
        for model in config.known_models:
            self._index_bare_model(
                self._bare_known,
                self._routable_bare_known,
                model,
                config,
                account_routes,
            )

    @staticmethod
    def _index_bare_model(
        candidates: set[str],
        routable: set[str],
        model: str,
        config: ProviderConfig,
        account_routes: _AccountRouteIndex,
    ) -> None:
        for name in _matching_model_names(config.prefix, model):
            candidates.add(name)
            if account_routes.supports(name):
                routable.add(name)

    def _prefix_supports(self, prefix: str, model: str) -> bool:
        if prefix in self._prefixes_accepting_any_model:
            return True
        route_models = self._prefix_route_models.get(prefix)
        if route_models is not None and model in route_models:
            return True
        patterns = self._prefix_unrestricted_patterns.get(prefix)
        return patterns is not None and model_allowed(model, patterns)

    def has_route(self, model_str: str) -> bool:
        if "/" in model_str:
            requested_prefix, model = model_str.split("/", 1)
            prefix = PREFIX_ALIASES.get(requested_prefix, requested_prefix)
            configs = self._providers.get(prefix)
            if configs:
                return self._prefix_supports(prefix, model)
            if model_str in self._bare_defaults:
                return model_str in self._routable_bare_defaults
            return model_str in self._routable_bare_known

        prefix = PREFIX_ALIASES.get(model_str, model_str)
        if prefix in self._prefixes_with_defaults:
            return prefix in self._prefixes_with_routable_defaults
        if model_str in self._bare_defaults:
            return model_str in self._routable_bare_defaults
        return model_str in self._routable_bare_known

    def register_combo(self, combo: ComboConfig) -> None:
        self._combos[combo.name] = combo.models

    def clear_combos(self) -> None:
        self._combos = {}

    def lookup(self, model_str: str) -> list[ResolvedTarget] | None:
        if "/" in model_str:
            requested_prefix, model = model_str.split("/", 1)
            prefix = PREFIX_ALIASES.get(requested_prefix, requested_prefix)
            configs = self._providers.get(prefix)
            if configs:
                candidates = [(config, model) for config in configs]
            else:
                candidates = [(config, model_str) for config in self._bare_model_configs(model_str)]
        else:
            model = model_str
            prefix = PREFIX_ALIASES.get(model, model)
            prefix_defaults = [
                config
                for config in self._providers.get(prefix, [])
                if config.default_model is not None
            ]
            if prefix_defaults:
                candidates = [
                    (config, _model_name(config.prefix, config.default_model or ""))
                    for config in prefix_defaults
                ]
            else:
                candidates = [(config, model) for config in self._bare_model_configs(model)]
        if not candidates:
            return None
        results: list[ResolvedTarget] = []
        for config, resolved_model in candidates:
            if not _account_supports(config, resolved_model):
                continue
            native = _native_format(config.api_type)
            results.append(
                ResolvedTarget(
                    prefix=config.prefix,
                    model=resolved_model,
                    provider_config=config,
                    native_format=native,
                    account_id=config.upstream_key_id or config.id,
                )
            )
        if not results:
            return None
        return results

    def _bare_model_configs(self, model: str) -> list[ProviderConfig]:
        configs = [config for providers in self._providers.values() for config in providers]
        default_matches = [
            config
            for config in configs
            if config.default_model is not None
            and _model_id_matches(model, config.prefix, config.default_model)
        ]
        if default_matches:
            return default_matches
        return [
            config
            for config in configs
            if any(
                _model_id_matches(model, config.prefix, candidate)
                for candidate in config.known_models
            )
        ]

    def lookup_combo(self, name: str) -> list[str] | None:
        return self._combos.get(name)

    @property
    def providers(self) -> dict[str, list[ProviderConfig]]:
        return self._providers

    @property
    def combos(self) -> dict[str, list[str]]:
        return self._combos
