from __future__ import annotations

from fastapi import FastAPI

from janus.api.routes import router
from janus.config.schema import JanusConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler


def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0")
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()
    app.state.registry = registry
    app.state.config = config
    if config.providers:
        for pc in config.providers:
            registry.register(pc)
    if config.combos:
        for combo in config.combos:
            registry.register_combo(combo)
    app.state.fallback_handler = FallbackHandler(registry)
    app.include_router(router, prefix="/v1")
    return app
