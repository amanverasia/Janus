from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from janus.catalog import PROVIDERS
from janus.providers.registry import model_allowed as provider_model_allowed
from janus.routing.model_caps import get_model_capabilities
from janus.storage.database import get_connection

VisibilityScope = Literal["provider", "models"]
ProviderMatch = Literal["auto", "catalog", "prefix"]

_NONE_SELECTED = "__janus_no_models_selected__"
_SOURCE_PRIORITY = {"configured": 0, "discovered": 1, "custom": 2}


class ModelCatalogRow(TypedDict):
    provider: str
    provider_id: str
    prefix: str
    id: str
    namespaced: str
    source: str
    disabled: bool
    selected: bool
    default: bool
    context_window: int | None
    max_output_tokens: int | None
    input_modalities: list[str]
    reasoning_efforts: list[str]
    capabilities: dict[str, Any]
    custom_id: str | None
    display_name: str | None


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value


def _string_list(value: Any) -> list[str]:
    decoded = _json_value(value, [])
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, str) and item]


def _boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _capabilities(value: Any, *, prefix: str, model: str) -> dict[str, Any]:
    decoded = _json_value(value, None)
    defaults = get_model_capabilities(prefix, model)
    if isinstance(decoded, dict):
        defaults.update({str(key): item for key, item in decoded.items()})
    elif isinstance(decoded, list):
        defaults["reported"] = decoded
    return defaults


def _input_modalities(value: Any, capabilities: dict[str, Any]) -> list[str]:
    configured = _string_list(value)
    if configured:
        return configured
    result = ["text"]
    if capabilities.get("vision") or capabilities.get("image_input"):
        result.append("image")
    if capabilities.get("audio_input"):
        result.append("audio")
    return result


def _reasoning_efforts(value: Any, capabilities: dict[str, Any]) -> list[str]:
    configured = _string_list(value)
    if configured:
        return configured
    if capabilities.get("reasoning") or capabilities.get("thinking"):
        return ["low", "medium", "high"]
    return []


def _inventory_routes() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for inventory_id, entry in PROVIDERS.items():
        gateway = entry.get("gateway")
        if not isinstance(gateway, dict):
            continue
        provider_id = str(gateway.get("id") or inventory_id)
        prefix = str(gateway.get("prefix") or provider_id)
        result[inventory_id] = (provider_id, prefix)
    return result


def _selected(selected_models: list[str], *, prefix: str, model: str) -> bool:
    if not selected_models:
        return True
    return model in selected_models or f"{prefix}/{model}" in selected_models


def _merge_row(
    rows: dict[str, ModelCatalogRow],
    *,
    provider: str,
    provider_id: str,
    prefix: str,
    model: str,
    source: str,
    provider_enabled: bool,
    selected_models: list[str],
    default_model: str | None,
    display_name: str | None = None,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    input_modalities: Any = None,
    reasoning_efforts: Any = None,
    capabilities: Any = None,
    custom_id: str | None = None,
    custom_enabled: bool = True,
) -> None:
    if not model:
        return
    namespaced = f"{prefix}/{model}"
    existing = rows.get(namespaced)
    resolved_caps = _capabilities(capabilities, prefix=prefix, model=model)
    selected = _selected(selected_models, prefix=prefix, model=model) and custom_enabled
    row: ModelCatalogRow = {
        "provider": provider,
        "provider_id": provider_id,
        "prefix": prefix,
        "id": model,
        "namespaced": namespaced,
        "source": source,
        "disabled": not provider_enabled or not selected,
        "selected": selected,
        "default": model == default_model or namespaced == default_model,
        "context_window": context_window
        if context_window is not None
        else int(resolved_caps["context_window"])
        if isinstance(resolved_caps.get("context_window"), int)
        else None,
        "max_output_tokens": max_output_tokens
        if max_output_tokens is not None
        else int(resolved_caps["max_output"])
        if isinstance(resolved_caps.get("max_output"), int)
        else None,
        "input_modalities": _input_modalities(input_modalities, resolved_caps),
        "reasoning_efforts": _reasoning_efforts(reasoning_efforts, resolved_caps),
        "capabilities": resolved_caps,
        "custom_id": custom_id,
        "display_name": display_name,
    }
    if existing is not None:
        existing_eligible = not existing["disabled"]
        candidate_eligible = not row["disabled"]
        use_candidate_owner = candidate_eligible and (
            not existing_eligible or row["provider_id"] < existing["provider_id"]
        )
        if not use_candidate_owner:
            row["provider"] = existing["provider"]
            row["provider_id"] = existing["provider_id"]
            row["prefix"] = existing["prefix"]
            if row["source"] == existing["source"] == "custom":
                row["custom_id"] = existing["custom_id"]
                row["display_name"] = existing["display_name"]
                row["context_window"] = existing["context_window"]
                row["max_output_tokens"] = existing["max_output_tokens"]
                row["input_modalities"] = existing["input_modalities"]
                row["reasoning_efforts"] = existing["reasoning_efforts"]
                row["capabilities"] = existing["capabilities"]
        if row["display_name"] is None:
            row["display_name"] = existing["display_name"]
        if context_window is None:
            row["context_window"] = existing["context_window"]
        if max_output_tokens is None:
            row["max_output_tokens"] = existing["max_output_tokens"]
        if _SOURCE_PRIORITY[row["source"]] < _SOURCE_PRIORITY[existing["source"]]:
            row["source"] = existing["source"]
            row["custom_id"] = existing["custom_id"]
        merged_caps = dict(existing["capabilities"])
        merged_caps.update(row["capabilities"])
        row["capabilities"] = merged_caps
        row["default"] = row["default"] or existing["default"]
        row["selected"] = row["selected"] or existing["selected"]
        row["disabled"] = row["disabled"] and existing["disabled"]
    rows[namespaced] = row


async def _table_exists(db_path: str | Path, table: str) -> bool:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def list_catalog_models(
    db_path: str | Path,
    *,
    include_disabled: bool = True,
) -> list[ModelCatalogRow]:
    rows: dict[str, ModelCatalogRow] = {}
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM providers ORDER BY id") as cursor:
            provider_rows = [dict(row) for row in await cursor.fetchall()]

    providers_by_id = {str(row["id"]): row for row in provider_rows}
    providers_by_prefix: dict[str, list[dict[str, Any]]] = {}
    for provider_row in provider_rows:
        prefix = str(provider_row["prefix"])
        providers_by_prefix.setdefault(prefix, []).append(provider_row)
        provider_id = str(provider_row["id"])
        provider = str(provider_row.get("catalog_id") or provider_id)
        selected_models = _string_list(provider_row.get("selected_models"))
        default_model = provider_row.get("default_model")
        default_value = str(default_model) if default_model else None
        enabled = _boolean(provider_row.get("is_enabled"), default=True)
        for model in _string_list(provider_row.get("models")):
            if not provider_model_allowed(model, _string_list(provider_row.get("allowed_models"))):
                continue
            _merge_row(
                rows,
                provider=provider,
                provider_id=provider_id,
                prefix=prefix,
                model=model,
                source="configured",
                provider_enabled=enabled,
                selected_models=selected_models,
                default_model=default_value,
            )

    inventory_routes = _inventory_routes()
    if await _table_exists(db_path, "upstream_models"):
        async with get_connection(db_path) as db:
            async with db.execute(
                "SELECT * FROM upstream_models WHERE is_available = 1 ORDER BY created_at, id"
            ) as cursor:
                discovered_rows = [dict(row) for row in await cursor.fetchall()]
        for discovered in discovered_rows:
            inventory_id = str(discovered["provider_id"])
            route = inventory_routes.get(inventory_id, (inventory_id, inventory_id))
            catalog_provider_id, prefix = route
            routed_providers = providers_by_prefix.get(prefix, [])
            direct_provider = providers_by_id.get(catalog_provider_id)
            if not routed_providers and direct_provider is not None:
                routed_providers = [direct_provider]
            for routed_provider in routed_providers:
                if not _boolean(routed_provider.get("live_models"), default=True):
                    continue
                model_id = str(discovered.get("model_id") or "")
                if not provider_model_allowed(
                    model_id, _string_list(routed_provider.get("allowed_models"))
                ):
                    continue
                provider_id = str(routed_provider["id"])
                selected_models = _string_list(routed_provider.get("selected_models"))
                default_model = routed_provider.get("default_model")
                _merge_row(
                    rows,
                    provider=str(routed_provider.get("catalog_id") or catalog_provider_id),
                    provider_id=provider_id,
                    prefix=str(routed_provider["prefix"]),
                    model=model_id,
                    source="discovered",
                    provider_enabled=_boolean(routed_provider.get("is_enabled"), default=True),
                    selected_models=selected_models,
                    default_model=str(default_model) if default_model else None,
                    display_name=str(discovered["display_name"])
                    if discovered.get("display_name")
                    else None,
                    context_window=discovered.get("context_window"),
                    max_output_tokens=discovered.get("max_output_tokens"),
                    capabilities=discovered.get("capabilities"),
                )

    if await _table_exists(db_path, "custom_models"):
        async with get_connection(db_path) as db:
            async with db.execute("SELECT * FROM custom_models ORDER BY created_at, id") as cursor:
                custom_rows = [dict(row) for row in await cursor.fetchall()]
        for custom in custom_rows:
            provider_id = str(custom["provider_id"])
            custom_provider = providers_by_id.get(provider_id)
            if custom_provider is None:
                continue
            model_id = str(custom.get("model_id") or "")
            if not provider_model_allowed(
                model_id, _string_list(custom_provider.get("allowed_models"))
            ):
                continue
            custom_enabled = _boolean(custom.get("is_enabled"), default=True)
            namespaced = f"{custom_provider['prefix']}/{model_id}"
            if not custom_enabled and namespaced in rows:
                continue
            selected_models = _string_list(custom_provider.get("selected_models"))
            default_model = custom_provider.get("default_model")
            _merge_row(
                rows,
                provider=str(custom_provider.get("catalog_id") or provider_id),
                provider_id=provider_id,
                prefix=str(custom_provider["prefix"]),
                model=model_id,
                source="custom",
                provider_enabled=_boolean(custom_provider.get("is_enabled"), default=True),
                selected_models=selected_models,
                default_model=str(default_model) if default_model else None,
                display_name=str(custom["display_name"]) if custom.get("display_name") else None,
                context_window=custom.get("context_window"),
                max_output_tokens=custom.get("max_output_tokens"),
                input_modalities=custom.get("input_modalities"),
                reasoning_efforts=custom.get("reasoning_efforts"),
                capabilities=custom.get("capabilities"),
                custom_id=str(custom["id"]),
                custom_enabled=custom_enabled,
            )

    catalog = sorted(rows.values(), key=lambda row: (row["prefix"], row["id"]))
    if include_disabled:
        return catalog
    return [row for row in catalog if not row["disabled"]]


async def resolve_provider_models(
    db_path: str | Path,
    provider: str,
    *,
    match: ProviderMatch = "auto",
) -> tuple[list[str], str, list[str]]:
    params: tuple[str, ...]
    if match == "catalog":
        query = "SELECT * FROM providers WHERE catalog_id = ? ORDER BY id"
        params = (provider,)
    elif match == "prefix":
        query = "SELECT * FROM providers WHERE prefix = ? ORDER BY id"
        params = (provider,)
    else:
        query = """SELECT * FROM providers
                   WHERE id = ? OR catalog_id = ? OR prefix = ?
                   ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, id"""
        params = (provider, provider, provider, provider)
    async with get_connection(db_path) as db:
        async with db.execute(query, params) as cursor:
            raw_provider = await cursor.fetchone()
    if raw_provider is None:
        raise LookupError("Provider not found")
    provider_row = dict(raw_provider)
    prefix = str(provider_row["prefix"])
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM providers WHERE prefix = ? ORDER BY id", (prefix,)
        ) as cursor:
            prefix_providers = [dict(row) for row in await cursor.fetchall()]
    target_providers = (
        [provider_row]
        if match == "auto" and provider == str(provider_row["id"])
        else prefix_providers
    )
    provider_ids = [str(row["id"]) for row in target_providers]
    known = {model for row in target_providers for model in _string_list(row.get("models"))}

    if any(
        _boolean(row.get("live_models"), default=True) for row in target_providers
    ) and await _table_exists(db_path, "upstream_models"):
        inventory_ids = {
            inventory_id
            for inventory_id, (_catalog_id, route_prefix) in _inventory_routes().items()
            if route_prefix == prefix
        }
        inventory_ids.add(prefix)
        if inventory_ids:
            placeholders = ",".join("?" for _ in inventory_ids)
            async with get_connection(db_path) as db:
                async with db.execute(
                    f"""SELECT DISTINCT model_id FROM upstream_models
                        WHERE is_available = 1 AND provider_id IN ({placeholders})""",
                    sorted(inventory_ids),
                ) as cursor:
                    known.update(str(row["model_id"]) for row in await cursor.fetchall())

    if await _table_exists(db_path, "custom_models"):
        placeholders = ",".join("?" for _ in provider_ids)
        if placeholders:
            async with get_connection(db_path) as db:
                async with db.execute(
                    f"""SELECT model_id FROM custom_models
                        WHERE provider_id IN ({placeholders}) AND is_enabled = 1""",
                    provider_ids,
                ) as cursor:
                    known.update(str(row["model_id"]) for row in await cursor.fetchall())
    return provider_ids, prefix, sorted(known)


async def set_model_visibility(
    db_path: str | Path,
    *,
    scope: VisibilityScope,
    provider: str,
    provider_match: ProviderMatch = "auto",
    targets: list[str],
    enabled: bool,
) -> list[ModelCatalogRow]:
    provider_ids, prefix, known_models = await resolve_provider_models(
        db_path, provider, match=provider_match
    )
    known = set(known_models)
    normalized_targets = {
        target[len(prefix) + 1 :] if target.startswith(f"{prefix}/") else target
        for target in targets
    }
    unknown = sorted(normalized_targets - known)
    if unknown:
        raise ValueError(f"Unknown model target: {unknown[0]}")

    async with get_connection(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in provider_ids)
        async with db.execute(
            f"SELECT selected_models FROM providers WHERE id IN ({placeholders})",
            provider_ids,
        ) as cursor:
            provider_rows = await cursor.fetchall()
        current_lists = [_string_list(row["selected_models"]) for row in provider_rows]

        if scope == "provider":
            updated: list[str] = [] if enabled else [_NONE_SELECTED]
        else:
            if any(not current for current in current_lists):
                selected = set(known)
            else:
                selected = {
                    item[len(prefix) + 1 :] if item.startswith(f"{prefix}/") else item
                    for current in current_lists
                    for item in current
                    if item != _NONE_SELECTED
                }
            if enabled:
                selected.update(normalized_targets)
            else:
                selected.difference_update(normalized_targets)
            updated = sorted(selected) if selected else [_NONE_SELECTED]

        await db.execute(
            f"""UPDATE providers SET selected_models = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})""",
            [json.dumps(updated), *provider_ids],
        )
        await db.commit()

    return [
        row
        for row in await list_catalog_models(db_path, include_disabled=True)
        if row["prefix"] == prefix
    ]
