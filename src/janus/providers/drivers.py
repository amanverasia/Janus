from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from janus.config.schema import ProviderConfig

from .anthropic import AnthropicProvider
from .antigravity import AntigravityProvider
from .base import Provider
from .claude_oauth import ClaudeOAuthProvider
from .codex import CodexProvider
from .cursor import CursorProvider
from .gemini import GeminiProvider
from .github_copilot import GitHubCopilotProvider
from .kiro import KiroProvider
from .mimo_free import MimoFreeProvider
from .openai_compat import OpenAICompatProvider
from .opencode_free import OpenCodeFreeProvider


@dataclass(frozen=True)
class ProviderDriver:
    api_type: str
    native_format: str
    discovery: str | None
    builder: Callable[[ProviderConfig], Provider]


def _default_headers_for(config: ProviderConfig) -> dict[str, str] | None:
    from janus.catalog import PROVIDERS

    catalog_id = getattr(config, "catalog_id", None)
    candidates: list[dict[str, object]] = []
    for provider_id, entry in PROVIDERS.items():
        gateway = entry.get("gateway")
        if not isinstance(gateway, dict):
            continue
        if catalog_id and provider_id == catalog_id:
            candidates.insert(0, gateway)
            continue
        if gateway.get("prefix") == config.prefix or gateway.get("id") == config.row_id:
            candidates.append(gateway)
    for gateway in candidates:
        headers = gateway.get("default_headers")
        if isinstance(headers, dict):
            return {str(key): str(value) for key, value in headers.items()}
    return None


def _openai_compat(config: ProviderConfig) -> Provider:
    return OpenAICompatProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        default_headers=_default_headers_for(config),
    )


def _anthropic(config: ProviderConfig) -> Provider:
    return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url)


def _gemini(config: ProviderConfig) -> Provider:
    return GeminiProvider(api_key=config.api_key or "", base_url=config.base_url)


def _opencode_free(config: ProviderConfig) -> Provider:
    return OpenCodeFreeProvider()


def _mimo_free(config: ProviderConfig) -> Provider:
    return MimoFreeProvider()


def _github_copilot(config: ProviderConfig) -> Provider:
    return GitHubCopilotProvider(
        oauth_token=config.api_key or "",
        base_url=config.base_url,
    )


def _codex(config: ProviderConfig) -> Provider:
    return CodexProvider(api_key=config.api_key or "", base_url=config.base_url)


def _kiro(config: ProviderConfig) -> Provider:
    return KiroProvider(api_key=config.api_key or "", base_url=config.base_url)


def _cursor(config: ProviderConfig) -> Provider:
    return CursorProvider(api_key=config.api_key or "", base_url=config.base_url)


def _antigravity(config: ProviderConfig) -> Provider:
    variant = "gemini_cli" if "gemini" in config.api_type else "antigravity"
    return AntigravityProvider(
        api_key=config.api_key or "",
        base_url=config.base_url,
        credential_expires_at=config.credential_expires_at,
        variant=variant,
    )


def _claude_oauth(config: ProviderConfig) -> Provider:
    return ClaudeOAuthProvider(api_key=config.api_key or "", base_url=config.base_url)


_CANONICAL_DRIVERS = (
    ProviderDriver("openai_compat", "openai", "openai", _openai_compat),
    ProviderDriver("anthropic", "anthropic", "anthropic", _anthropic),
    ProviderDriver("gemini", "gemini", "gemini", _gemini),
    ProviderDriver("opencode_free", "openai", None, _opencode_free),
    ProviderDriver("mimo_free", "openai", None, _mimo_free),
    ProviderDriver("github_copilot", "openai", "github_copilot", _github_copilot),
    ProviderDriver("codex", "openai_responses", None, _codex),
    ProviderDriver("kiro", "openai", "kiro", _kiro),
    ProviderDriver("cursor", "openai", "cursor", _cursor),
    ProviderDriver("antigravity", "gemini", "antigravity", _antigravity),
    ProviderDriver("claude_oauth", "anthropic", None, _claude_oauth),
)

_ALIASES = {
    "gemini_cli": "antigravity",
    "gemini-cli": "antigravity",
    "claude": "claude_oauth",
}

DRIVERS: dict[str, ProviderDriver] = {driver.api_type: driver for driver in _CANONICAL_DRIVERS}
for alias, canonical in _ALIASES.items():
    base = DRIVERS[canonical]
    DRIVERS[alias] = ProviderDriver(alias, base.native_format, base.discovery, base.builder)


def get_driver(api_type: str) -> ProviderDriver | None:
    return DRIVERS.get(api_type)


def build_provider(config: ProviderConfig) -> Provider:
    driver = get_driver(config.api_type)
    if driver is None:
        raise ValueError(f"Unknown api_type: {config.api_type}")
    return driver.builder(config)


def native_format_for(api_type: str) -> str:
    driver = get_driver(api_type)
    if driver is not None:
        return driver.native_format
    return api_type.replace("_compat", "")


def supported_api_types() -> frozenset[str]:
    return frozenset(DRIVERS)
