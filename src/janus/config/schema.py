from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ServerSettings(BaseModel):
    port: int = 20128
    host: str = "127.0.0.1"
    require_api_key: bool = True
    data_dir: Path = Path.home() / ".janus"
    account_strategy: str = "round_robin"  # "fill_first" | "round_robin" | "sticky_rr"
    sticky_limit: int = 3


class ProviderConfig(BaseModel):
    id: str
    catalog_id: str | None = None
    prefix: str
    # openai_compat | anthropic | gemini | opencode_free | github_copilot |
    # codex | kiro | cursor | antigravity | claude_oauth
    api_type: str
    base_url: str
    api_key: str | None = None
    models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    live_models: bool = True
    selected_models: list[str] = Field(default_factory=list)
    custom_models: list[str] = Field(default_factory=list)
    discovered_models: list[str] | None = None
    allowed_models: list[str] = Field(default_factory=list)
    upstream_key_id: str | None = None
    credential_expires_at: float | None = None
    rate_limit_rpm: int | None = None
    rate_limit_rpd: int | None = None
    quota_window: str | None = None  # "5h" | "daily" | "weekly" | "monthly"
    quota_limit: int | None = None
    quota_metric: str = "requests"  # "requests" | "tokens"
    transports: dict[str, str] | None = None  # format -> base_url for multi-format providers

    @property
    def row_id(self) -> str:
        """Provider DB-row id (strips the inventory-key suffix)."""
        return self.id.split("::", 1)[0]

    @property
    def known_models(self) -> list[str]:
        """Ordered union of static, live-discovered, and custom model ids."""
        result: list[str] = []
        seen: set[str] = set()
        discovered = self.discovered_models or []
        for model in (*self.models, *self.custom_models, *discovered):
            if model in seen:
                continue
            seen.add(model)
            result.append(model)
        return result

    @property
    def visible_models(self) -> list[str]:
        """Known models exposed in discovery; selection does not deny direct routing."""
        known = self.known_models
        if not self.selected_models:
            return known
        selected = set(self.selected_models)
        return [
            model for model in known if model in selected or f"{self.prefix}/{model}" in selected
        ]


class ComboConfig(BaseModel):
    name: str
    models: list[str]


class CustomModelConfig(BaseModel):
    id: str | None = None
    provider_id: str
    model_id: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] = Field(default_factory=list)
    reasoning_efforts: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True


class TokenSaverSettings(BaseModel):
    enabled: bool = False
    level: str = "full"


class TokenSaverConfig(BaseModel):
    rtk: TokenSaverSettings = Field(default_factory=lambda: TokenSaverSettings(enabled=True))
    caveman: TokenSaverSettings = Field(default_factory=TokenSaverSettings)
    ponytail: TokenSaverSettings = Field(default_factory=TokenSaverSettings)


class JanusConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    providers: list[ProviderConfig] = Field(default_factory=list)
    combos: list[ComboConfig] = Field(default_factory=list)
    custom_models: list[CustomModelConfig] = Field(default_factory=list)
    api_keys: list[str] = Field(default_factory=list)
    token_savers: TokenSaverConfig = Field(default_factory=TokenSaverConfig)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
