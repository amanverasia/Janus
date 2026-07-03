from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 20128
    host: str = "127.0.0.1"
    data_dir: str = "~/.janus"
    require_api_key: bool = True
    config_path: str = ""
    log_level: str = "info"

    model_config = {"env_prefix": "JANUS_", "env_file": ".env", "extra": "ignore"}
