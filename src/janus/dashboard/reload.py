from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from janus.app import _build_provider
from janus.config.schema import ComboConfig, ProviderConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.combos_db import list_combos
from janus.storage.pricing_db import get_pricing_overrides
from janus.storage.providers_db import list_providers
from janus.storage.settings import get_all_settings
from janus.tokensavers.base import TokenSaver
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.ponytail import PonytailSaver
from janus.tokensavers.rtk import RTKSaver


async def reload_providers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_providers(db_path, enabled_only=True)

    old_providers: dict[str, Provider] = getattr(app.state, "providers", {})

    registry = ProviderRegistry()
    new_providers: dict[str, Provider] = {}

    for row in rows:
        models = json.loads(row["models"]) if row["models"] else []
        pc = ProviderConfig(
            id=row["id"],
            prefix=row["prefix"],
            api_type=row["api_type"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            models=models,
        )
        registry.register(pc)
        new_providers[row["id"]] = _build_provider(pc)

    for old_id, old_provider in old_providers.items():
        if old_id not in new_providers:
            await old_provider.close()

    app.state.providers = new_providers
    app.state.registry = registry
    app.state.fallback_handler = FallbackHandler(registry)


async def reload_combos(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    rows = await list_combos(db_path)
    registry: ProviderRegistry = app.state.registry
    registry._combos = {}
    for row in rows:
        models = json.loads(row["models"]) if row["models"] else []
        registry.register_combo(ComboConfig(name=row["name"], models=models))


async def reload_savers(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    settings = await get_all_settings(db_path)
    savers: list[TokenSaver] = []
    if settings.get("saver_rtk_enabled", "true").lower() == "true":
        savers.append(RTKSaver())
    if settings.get("saver_caveman_enabled", "false").lower() == "true":
        savers.append(CavemanSaver())
    if settings.get("saver_ponytail_enabled", "false").lower() == "true":
        level = settings.get("saver_ponytail_level", "full")
        savers.append(PonytailSaver(level=level))
    app.state.saver_pipeline = SaverPipeline(savers)


async def reload_pricing(app: FastAPI) -> None:
    db_path: Path = app.state.db_path
    overrides = await get_pricing_overrides(db_path)
    app.state.pricing_registry = PricingRegistry(overrides)
