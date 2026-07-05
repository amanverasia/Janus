from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ServerSettings(BaseModel):
    port: int = 20128
    host: str = "127.0.0.1"
    require_api_key: bool = True
    data_dir: Path = Path.home() / ".janus"


class ProviderConfig(BaseModel):
    id: str
    prefix: str
    api_type: str  # "openai_compat" | "anthropic" | "gemini" | "opencode_free" | "github_copilot"
    base_url: str
    api_key: str | None = None
    models: list[str] = Field(default_factory=list)
    upstream_key_id: str | None = None
    rate_limit_rpm: int | None = None
    rate_limit_rpd: int | None = None


class ComboConfig(BaseModel):
    name: str
    models: list[str]


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
    api_keys: list[str] = Field(default_factory=list)
    token_savers: TokenSaverConfig = Field(default_factory=TokenSaverConfig)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
