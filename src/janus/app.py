from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.staticfiles import PathLike
from starlette.types import Scope

from janus.api.routes import gemini_router, ollama_router, router
from janus.config.schema import JanusConfig, ProviderConfig
from janus.inventory.key_encryption import CredentialEncryptionError
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.providers.drivers import build_provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.provider_snapshots import ProviderSnapshot, close_provider_snapshots
from janus.storage.database import init_db, seed_from_config
from janus.tokensavers.pipeline import SaverPipeline

logger = logging.getLogger(__name__)


class _CachedDashboardStaticFiles(StaticFiles):
    """Static file mount that caches SvelteKit content-hashed assets forever.

    SvelteKit emits content-hashed bundles under `_app/immutable/`; the hash
    changes when content does, so aggressive caching is safe and avoids
    per-navigation revalidation. `index.html` and `version.json` stay
    revalidated so the SPA picks up deploys immediately.
    """

    _IMMUTABLE_PREFIX = "_app/immutable/"
    _IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
    _DEFAULT_CACHE = "no-cache"

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        relative = str(scope.get("path", "")).removeprefix("/dashboard/static/app/")
        cache = (
            self._IMMUTABLE_CACHE
            if relative.startswith(self._IMMUTABLE_PREFIX)
            else self._DEFAULT_CACHE
        )
        response.headers["cache-control"] = cache
        return response


def _build_provider(config: ProviderConfig) -> Provider:
    return build_provider(config)


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


async def _backfill_provider_keys(db_path: Path) -> None:
    from janus.inventory.provider_key_sync import backfill_provider_keys

    try:
        await backfill_provider_keys(db_path)
    except Exception:
        logger.warning("Provider key backfill failed", exc_info=True)


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
    await _backfill_provider_keys(db_path)
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

    await close_provider_snapshots(app)


def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="3.1.0", lifespan=lifespan)
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
    app.state.model_catalog = []
    app.state.provider_snapshot = ProviderSnapshot(
        providers=app.state.providers,
        registry=app.state.registry,
        handler=app.state.fallback_handler,
    )
    app.state.retired_provider_snapshots = []
    from janus.api.rate_limit import GatewayRateLimiter

    app.state.gateway_rate_limiter = GatewayRateLimiter()

    @app.exception_handler(CredentialEncryptionError)
    async def handle_credential_encryption_error(
        request: Request, exc: CredentialEncryptionError
    ) -> JSONResponse:
        logger.error(
            "Credential encryption configuration failed for request path %s: %s",
            request.url.path,
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "type": "credential_encryption_error",
                    "message": str(exc),
                    "hint": (
                        "Verify INVENTORY_ENCRYPTION_KEY is set to the key used to "
                        "encrypt stored credentials."
                    ),
                }
            },
        )

    from janus.dashboard.live import LiveTrackingMiddleware, get_bus

    app.add_middleware(LiveTrackingMiddleware, bus=get_bus())

    app.include_router(router, prefix="/v1")
    app.include_router(gemini_router)
    app.include_router(ollama_router)

    from janus.dashboard.api_v2 import router as dashboard_api_v2_router
    from janus.dashboard.inventory_push_routes import router as inventory_push_router
    from janus.dashboard.inventory_routes import router as inventory_router
    from janus.dashboard.routes import dashboard_page_redirect_router
    from janus.dashboard.routes import router as dashboard_router
    from janus.dashboard.ui_routes import router as dashboard_ui_router

    app.include_router(dashboard_page_redirect_router, prefix="/dashboard")
    app.include_router(dashboard_router, prefix="/dashboard")
    app.include_router(inventory_router, prefix="/dashboard")
    app.include_router(inventory_push_router, prefix="/dashboard/api/inventory")
    app.include_router(dashboard_api_v2_router, prefix="/dashboard")
    app.include_router(dashboard_ui_router, prefix="/dashboard")

    dashboard_static = Path(__file__).parent / "dashboard" / "static"
    app.mount(
        "/dashboard/static",
        _CachedDashboardStaticFiles(directory=str(dashboard_static)),
        name="dashboard_static",
    )

    @app.get("/")
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/ui", status_code=308)

    return app
