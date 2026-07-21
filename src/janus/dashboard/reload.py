from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from janus.app import _build_provider
from janus.config.schema import ComboConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
from janus.routing.upstream_expand import expand_gateway_provider
from janus.storage.combos_db import list_combos
from janus.storage.pricing_catalog import get_catalog
from janus.storage.pricing_db import get_pricing_overrides
from janus.storage.providers_db import list_providers
from janus.storage.settings import ensure_saver_defaults, get_all_settings, resolve_saver_settings
from janus.storage.upstream_keys import list_routable_upstream_keys
from janus.tokensavers.base import AsyncTokenSaver, TokenSaver
from janus.tokensavers.caveman import PROMPTS as CAVEMAN_PROMPTS
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.headroom import HeadroomSaver
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.ponytail import PROMPTS as PONYTAIL_PROMPTS
from janus.tokensavers.ponytail import PonytailSaver
from janus.tokensavers.rtk import RTKSaver


async def reload_providers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_providers(db_path, enabled_only=True)

    old_providers: dict[str, Provider] = getattr(app.state, "providers", {})

    registry = ProviderRegistry()
    new_providers: dict[str, Provider] = {}

    for row in rows:
        inventory_id = inventory_provider_id_for_prefix(row["prefix"])
        upstream_keys = await list_routable_upstream_keys(db_path, inventory_id)
        for pc in expand_gateway_provider(row, upstream_keys):
            registry.register(pc)
            new_providers[pc.id] = _build_provider(pc)

    combo_rows = await list_combos(db_path)
    for row in combo_rows:
        models = json.loads(row["models"]) if row["models"] else []
        registry.register_combo(ComboConfig(name=row["name"], models=models))

    for old_id, old_provider in old_providers.items():
        if old_id not in new_providers:
            await old_provider.close()

    app.state.providers = new_providers
    app.state.registry = registry
    old_handler: FallbackHandler | None = getattr(app.state, "fallback_handler", None)
    handler = FallbackHandler(registry, db_path=db_path)
    if isinstance(old_handler, FallbackHandler):
        handler.adopt_runtime_state(old_handler)
    app.state.fallback_handler = handler
    await app.state.fallback_handler.load_cooldowns()
    await app.state.fallback_handler.load_request_counts()
    await app.state.fallback_handler.load_quota_usage()


async def reload_combos(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_combos(db_path)
    registry: ProviderRegistry = app.state.registry
    registry.clear_combos()
    for row in rows:
        models = json.loads(row["models"]) if row["models"] else []
        registry.register_combo(ComboConfig(name=row["name"], models=models))


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
