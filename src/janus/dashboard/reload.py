from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from janus.app import _build_provider
from janus.config.schema import ComboConfig, ProviderConfig
from janus.models.catalog import list_catalog_models
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
from janus.routing.provider_snapshots import (
    ProviderSnapshot,
    ensure_provider_snapshot,
    install_provider_snapshot,
)
from janus.routing.upstream_expand import expand_gateway_provider
from janus.storage.combos_db import list_combos
from janus.storage.custom_models import list_custom_models
from janus.storage.pricing_catalog import get_catalog
from janus.storage.pricing_db import get_pricing_overrides
from janus.storage.providers_db import list_providers
from janus.storage.settings import (
    cooldowns_enabled,
    ensure_saver_defaults,
    get_all_settings,
    resolve_saver_settings,
)
from janus.storage.upstream_keys import list_routable_upstream_keys
from janus.storage.upstream_models import list_model_ids_for_keys
from janus.tokensavers.base import AsyncTokenSaver, TokenSaver
from janus.tokensavers.caveman import PROMPTS as CAVEMAN_PROMPTS
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.headroom import HeadroomSaver
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.ponytail import PROMPTS as PONYTAIL_PROMPTS
from janus.tokensavers.ponytail import PonytailSaver
from janus.tokensavers.rtk import RTKSaver


def _provider_execution_key(config: ProviderConfig) -> tuple[Any, ...]:
    return (
        config.id,
        config.catalog_id,
        config.prefix,
        config.api_type,
        config.base_url,
        config.api_key,
        config.credential_expires_at,
    )


async def reload_providers(app: FastAPI) -> None:
    lock: asyncio.Lock | None = getattr(app.state, "provider_reload_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.provider_reload_lock = lock
    async with lock:
        await _reload_providers_locked(app)


async def _reload_providers_locked(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_providers(db_path, enabled_only=True)
    old_providers: dict[str, Provider] = getattr(app.state, "providers", {})
    old_registry: ProviderRegistry | None = getattr(app.state, "registry", None)
    old_configs = (
        {config.id: config for configs in old_registry.providers.values() for config in configs}
        if isinstance(old_registry, ProviderRegistry)
        else {}
    )
    registry = ProviderRegistry()
    new_providers: dict[str, Provider] = {}
    built_providers: list[Provider] = []
    reused_provider_ids: set[str] = set()
    old_handler: FallbackHandler | None = getattr(app.state, "fallback_handler", None)
    keys_by_inventory: dict[str, list[dict[str, Any]]] = {}
    all_key_ids: list[str] = []
    for row in rows:
        inventory_id = inventory_provider_id_for_prefix(row["prefix"])
        if inventory_id not in keys_by_inventory:
            keys = await list_routable_upstream_keys(db_path, inventory_id)
            keys_by_inventory[inventory_id] = keys
            all_key_ids.extend(str(key["id"]) for key in keys)
    discoveries = await list_model_ids_for_keys(db_path, all_key_ids)
    custom_by_provider: dict[str, list[str]] = {}
    for model in await list_custom_models(db_path, enabled_only=True):
        custom_by_provider.setdefault(str(model["provider_id"]), []).append(str(model["model_id"]))

    try:
        for row in rows:
            inventory_id = inventory_provider_id_for_prefix(row["prefix"])
            for pc in expand_gateway_provider(
                row,
                keys_by_inventory[inventory_id],
                custom_models=custom_by_provider.get(str(row["id"]), []),
                discovered_models_by_key=discoveries,
            ):
                registry.register(pc)
                old_config = old_configs.get(pc.id)
                old_provider = old_providers.get(pc.id)
                if (
                    old_provider is not None
                    and old_config is not None
                    and _provider_execution_key(old_config) == _provider_execution_key(pc)
                ):
                    new_providers[pc.id] = old_provider
                    reused_provider_ids.add(pc.id)
                else:
                    provider = _build_provider(pc)
                    new_providers[pc.id] = provider
                    built_providers.append(provider)

        combo_rows = await list_combos(db_path)
        for row in combo_rows:
            models = json.loads(row["models"]) if row["models"] else []
            registry.register_combo(ComboConfig(name=row["name"], models=models))

        handler = FallbackHandler(registry, db_path=db_path)
        if isinstance(old_handler, FallbackHandler):
            handler.adopt_runtime_state(old_handler)
        settings = await get_all_settings(db_path)
        handler.cooldowns_enabled = cooldowns_enabled(settings)
        await handler.load_cooldowns()
        await handler.load_request_counts()
        await handler.load_quota_usage()
        model_catalog = await list_catalog_models(db_path, include_disabled=True)
    except Exception:
        await asyncio.gather(
            *(provider.close() for provider in built_providers),
            return_exceptions=True,
        )
        raise

    providers_to_close = [
        provider
        for provider_id, provider in old_providers.items()
        if provider_id not in reused_provider_ids
    ]
    await install_provider_snapshot(
        app,
        ProviderSnapshot(
            providers=new_providers,
            registry=registry,
            handler=handler,
            model_catalog=model_catalog,
        ),
        providers_to_close=providers_to_close,
    )


async def reload_combos(app: FastAPI) -> None:
    lock: asyncio.Lock | None = getattr(app.state, "provider_reload_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.provider_reload_lock = lock
    async with lock:
        current = ensure_provider_snapshot(app)
        registry = ProviderRegistry()
        for configs in current.registry.providers.values():
            for config in configs:
                registry.register(config)
        for row in await list_combos(app.state.db_path):
            models = json.loads(row["models"]) if row["models"] else []
            registry.register_combo(ComboConfig(name=row["name"], models=models))
        handler = FallbackHandler(registry, db_path=app.state.db_path)
        handler.adopt_runtime_state(current.handler)
        handler.cooldowns_enabled = current.handler.cooldowns_enabled
        await install_provider_snapshot(
            app,
            ProviderSnapshot(
                providers=current.providers,
                registry=registry,
                handler=handler,
                model_catalog=current.model_catalog,
            ),
            providers_to_close=[],
        )


async def reload_savers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    await ensure_saver_defaults(db_path)
    settings = resolve_saver_settings(await get_all_settings(db_path))
    savers: list[TokenSaver] = []
    async_savers: list[AsyncTokenSaver] = []
    if settings["saver_headroom_enabled"].lower() == "true":
        async_savers.append(HeadroomSaver(base_url=settings["saver_headroom_url"]))
    if settings["saver_rtk_enabled"].lower() == "true":
        savers.append(RTKSaver())
    if settings["saver_caveman_enabled"].lower() == "true":
        caveman_level = settings["saver_caveman_level"]
        if caveman_level not in CAVEMAN_PROMPTS:
            caveman_level = "full"
        savers.append(CavemanSaver(level=caveman_level))
    if settings["saver_ponytail_enabled"].lower() == "true":
        ponytail_level = settings["saver_ponytail_level"]
        if ponytail_level not in PONYTAIL_PROMPTS:
            ponytail_level = "full"
        savers.append(PonytailSaver(level=ponytail_level))
    old_pipeline: SaverPipeline | None = getattr(app.state, "saver_pipeline", None)
    new_pipeline = SaverPipeline(savers, async_savers)
    if old_pipeline is not None:
        new_pipeline.adopt_stats(old_pipeline)
        await old_pipeline.close()
    app.state.saver_pipeline = new_pipeline


async def reload_pricing(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    overrides = await get_pricing_overrides(db_path)
    catalog = await get_catalog(db_path)
    app.state.pricing_registry = PricingRegistry(overrides, catalog)
