from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from janus.api.routes import gemini_router, ollama_router, router
from janus.config.schema import JanusConfig, ProviderConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.anthropic import AnthropicProvider
from janus.providers.antigravity import AntigravityProvider
from janus.providers.base import Provider
from janus.providers.claude_oauth import ClaudeOAuthProvider
from janus.providers.codex import CodexProvider
from janus.providers.cursor import CursorProvider
from janus.providers.gemini import GeminiProvider
from janus.providers.github_copilot import GitHubCopilotProvider
from janus.providers.kiro import KiroProvider
from janus.providers.mimo_free import MimoFreeProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.database import init_db, seed_from_config
from janus.tokensavers.pipeline import SaverPipeline

logger = logging.getLogger(__name__)


def _default_headers_for(config: ProviderConfig) -> dict[str, str] | None:
    from janus.catalog import PROVIDERS

    for entry in PROVIDERS.values():
        gateway = entry.get("gateway")
        if not isinstance(gateway, dict):
            continue
        if gateway.get("prefix") != config.prefix and gateway.get("id") != config.row_id:
            continue
        headers = gateway.get("default_headers")
        if isinstance(headers, dict):
            return {str(k): str(v) for k, v in headers.items()}
    return None


def _build_provider(config: ProviderConfig) -> Provider:
    if config.api_type == "opencode_free":
        return OpenCodeFreeProvider()
    if config.api_type == "mimo_free":
        return MimoFreeProvider()
    if config.api_type == "openai_compat":
        return OpenAICompatProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            default_headers=_default_headers_for(config),
        )
    if config.api_type == "anthropic":
        return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "gemini":
        return GeminiProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "github_copilot":
        return GitHubCopilotProvider(
            oauth_token=config.api_key or "",
            base_url=config.base_url,
        )
    if config.api_type == "codex":
        return CodexProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "kiro":
        return KiroProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "cursor":
        return CursorProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type in ("antigravity", "gemini_cli", "gemini-cli"):
        variant = "gemini_cli" if "gemini" in config.api_type else "antigravity"
        return AntigravityProvider(
            api_key=config.api_key or "",
            base_url=config.base_url,
            variant=variant,
        )
    if config.api_type in ("claude_oauth", "claude"):
        return ClaudeOAuthProvider(api_key=config.api_key or "", base_url=config.base_url)
    raise ValueError(f"Unknown api_type: {config.api_type}")


async def _initial_pricing_sync(app: FastAPI) -> None:
    """Fetch a fresh pricing catalog and reload the registry, fail-open.

    Runs as a background task off the critical startup path so a slow or
    failing upstream (LiteLLM/OpenRouter) never delays server readiness.
    """
    from janus.dashboard.reload import reload_pricing
    from janus.pricing.sync import PricingSyncError, fetch_and_sync

    try:
        await fetch_and_sync(app.state.db_path)
        await reload_pricing(app)
    except PricingSyncError as exc:
        logger.warning("Startup pricing sync failed: %s", exc)
    except Exception:
        logger.exception("Startup pricing sync raised an unexpected error")


async def _pricing_catalog_needs_sync(app: FastAPI) -> bool:
    from janus.pricing.scheduler import sync_interval_hours
    from janus.storage.pricing_catalog import catalog_count
    from janus.storage.settings import get_setting

    db_path: Path = app.state.db_path
    if await catalog_count(db_path) == 0:
        return True
    last_sync_raw = await get_setting(db_path, "pricing_last_sync_at")
    if last_sync_raw is None:
        return True
    try:
        last_sync = datetime.fromisoformat(last_sync_raw)
    except ValueError:
        return True
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=UTC)
    age_hours = (datetime.now(UTC) - last_sync).total_seconds() / 3600
    return age_hours >= sync_interval_hours()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    await init_db(db_path)
    config: JanusConfig = app.state.config
    await seed_from_config(db_path, config)

    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.routing.reload_bridge import bind_reload_app

    bind_reload_app(app)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)
    await app.state.fallback_handler.load_cooldowns()
    await app.state.fallback_handler.load_request_counts()

    from janus.inventory.scheduler import run_inventory_scheduler, scheduler_enabled

    app.state.inventory_scheduler_stop = asyncio.Event()
    app.state.inventory_scheduler_task = None
    if scheduler_enabled():
        app.state.inventory_scheduler_task = asyncio.create_task(
            run_inventory_scheduler(app.state.db_path, app.state.inventory_scheduler_stop)
        )

    app.state.pricing_initial_sync_task = None
    if await _pricing_catalog_needs_sync(app):
        app.state.pricing_initial_sync_task = asyncio.create_task(_initial_pricing_sync(app))

    from janus.pricing.scheduler import pricing_scheduler_enabled, run_pricing_scheduler

    app.state.pricing_scheduler_stop = asyncio.Event()
    app.state.pricing_scheduler_task = None
    if pricing_scheduler_enabled():
        app.state.pricing_scheduler_task = asyncio.create_task(
            run_pricing_scheduler(app, app.state.pricing_scheduler_stop)
        )

    yield

    app.state.inventory_scheduler_stop.set()
    scheduler_task = app.state.inventory_scheduler_task
    if scheduler_task is not None:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    app.state.pricing_scheduler_stop.set()
    pricing_scheduler_task = app.state.pricing_scheduler_task
    if pricing_scheduler_task is not None:
        pricing_scheduler_task.cancel()
        try:
            await pricing_scheduler_task
        except asyncio.CancelledError:
            pass

    initial_sync_task = app.state.pricing_initial_sync_task
    if initial_sync_task is not None:
        initial_sync_task.cancel()
        try:
            await initial_sync_task
        except asyncio.CancelledError:
            pass

    for provider in app.state.providers.values():
        await provider.close()


def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="1.1.0", lifespan=lifespan)
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()
    app.state.registry = registry
    app.state.config = config
    app.state.db_path = config.server.data_dir / "janus.db"
    app.state.fallback_handler = FallbackHandler(registry, db_path=app.state.db_path)
    app.state.saver_pipeline = SaverPipeline([])
    app.state.pricing_registry = PricingRegistry(config.pricing)
    app.state.providers = {}
    app.include_router(router, prefix="/v1")
    app.include_router(gemini_router)
    app.include_router(ollama_router)

    from janus.dashboard.inventory_push_routes import router as inventory_push_router
    from janus.dashboard.inventory_routes import router as inventory_router
    from janus.dashboard.routes import router as dashboard_router

    app.include_router(dashboard_router, prefix="/dashboard")
    app.include_router(inventory_router, prefix="/dashboard")
    app.include_router(inventory_push_router, prefix="/dashboard/api/inventory")

    dashboard_static = Path(__file__).parent / "dashboard" / "static"
    app.mount(
        "/dashboard/static",
        StaticFiles(directory=str(dashboard_static)),
        name="dashboard_static",
    )

    @app.get("/")
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    return app
