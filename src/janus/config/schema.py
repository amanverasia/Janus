from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ServerSettings(BaseModel):
    port: int = 20128
    host: str = "127.0.0.1"
    require_api_key: bool = False
    data_dir: Path = Path.home() / ".janus"


class ProviderConfig(BaseModel):
    id: str
    prefix: str
    api_type: str  # "openai_compat" | "anthropic" | "gemini" | "opencode_free"
    base_url: str
    api_key: str | None = None
    models: list[str] = Field(default_factory=list)


class JanusConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    providers: list[ProviderConfig] = Field(default_factory=list)
    api_keys: list[str] = Field(default_factory=list)
