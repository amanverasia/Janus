from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from janus.api.routes import router
from janus.config.schema import JanusConfig, ProviderConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.anthropic import AnthropicProvider
from janus.providers.base import Provider
from janus.providers.gemini import GeminiProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.database import init_db
from janus.tokensavers.base import TokenSaver
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.ponytail import PonytailSaver
from janus.tokensavers.rtk import RTKSaver


def _build_provider(config: ProviderConfig) -> Provider:
    if config.api_type == "opencode_free":
        return OpenCodeFreeProvider()
    if config.api_type == "openai_compat":
        return OpenAICompatProvider(base_url=config.base_url, api_key=config.api_key)
    if config.api_type == "anthropic":
        return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "gemini":
        return GeminiProvider(api_key=config.api_key or "")
    raise ValueError(f"Unknown api_type: {config.api_type}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    await init_db(db_path)
    yield
    for provider in app.state.providers.values():
        await provider.close()


def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0", lifespan=lifespan)
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()
    app.state.registry = registry
    app.state.config = config
    app.state.db_path = config.server.data_dir / "janus.db"
    if config.providers:
        for pc in config.providers:
            registry.register(pc)
    if config.combos:
        for combo in config.combos:
            registry.register_combo(combo)
    app.state.fallback_handler = FallbackHandler(registry)
    savers: list[TokenSaver] = []
    if config.token_savers.rtk.enabled:
        savers.append(RTKSaver())
    if config.token_savers.caveman.enabled:
        savers.append(CavemanSaver())
    if config.token_savers.ponytail.enabled:
        savers.append(PonytailSaver(level=config.token_savers.ponytail.level))
    app.state.saver_pipeline = SaverPipeline(savers)
    app.state.pricing_registry = PricingRegistry(config.pricing)
    providers: dict[str, Provider] = {}
    for configs in registry.providers.values():
        for pc in configs:
            providers[pc.id] = _build_provider(pc)
    app.state.providers = providers
    app.include_router(router, prefix="/v1")

    from janus.dashboard.routes import router as dashboard_router

    app.include_router(dashboard_router, prefix="/dashboard")
    return app
