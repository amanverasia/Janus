from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from janus.config.schema import ComboConfig, ProviderConfig

_API_TYPE_TO_NATIVE: dict[str, str] = {
    "openai_compat": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "opencode_free": "openai",
    "mimo_free": "openai",
    "github_copilot": "openai",
    "codex": "openai_responses",
    "kiro": "openai",
    "cursor": "openai",
    "antigravity": "gemini",
    "gemini_cli": "gemini",
    "gemini-cli": "gemini",
    "claude_oauth": "anthropic",
    "claude": "anthropic",
}


def _native_format(api_type: str) -> str:
    if api_type in _API_TYPE_TO_NATIVE:
        return _API_TYPE_TO_NATIVE[api_type]
    return api_type.replace("_compat", "")


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str
    account_id: str


# Client-facing prefix aliases → registered gateway prefix.
# e.g. "mimo/mimo-v2.5" routes like "xiaomi/mimo-v2.5".
PREFIX_ALIASES: dict[str, str] = {
    "mimo": "xiaomi",
}


def model_allowed(model: str, allowed: list[str]) -> bool:
    """True when `model` passes the allowlist.

    An empty allowlist means no restriction (current default behavior).
    Entries may be exact model names or fnmatch globs (e.g. "claude-opus-*").
    """
    if not allowed:
        return True
    return any(model == pattern or fnmatch.fnmatchcase(model, pattern) for pattern in allowed)


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
        prefix = PREFIX_ALIASES.get(prefix, prefix)
        configs = self._providers.get(prefix)
        if not configs:
            return None
        results: list[ResolvedTarget] = []
        for config in configs:
            if not model_allowed(rest, config.allowed_models):
                continue
            native = _native_format(config.api_type)
            results.append(
                ResolvedTarget(
                    prefix=prefix,
                    model=rest,
                    provider_config=config,
                    native_format=native,
                    account_id=config.upstream_key_id or config.id,
                )
            )
        if not results:
            return None
        return results

    def lookup_combo(self, name: str) -> list[str] | None:
        return self._combos.get(name)

    @property
    def providers(self) -> dict[str, list[ProviderConfig]]:
        return self._providers

    @property
    def combos(self) -> dict[str, list[str]]:
        return self._combos
