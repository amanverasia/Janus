from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from janus.dashboard.alerts import collect_dashboard_alerts
from janus.dashboard.auth import require_dashboard_access
from janus.dashboard.routes import (
    _api_v1_base_url,
    _build_budget_statuses,
    _enrich_providers,
    _ensure_db,
    _get_usage_stats_safe,
    _pricing_page_context,
    _request_logs_context,
    _savers_context,
    _wired_providers,
)
from janus.storage.analytics import (
    Dimension,
    get_breakdown,
    get_calendar_day_spend_summary,
    get_flow,
    get_leaderboard,
    get_spend_summary,
    get_success_rate,
)
from janus.storage.api_keys import create_key, list_keys
from janus.storage.budgets import create_or_update_budget, get_budget_status
from janus.storage.combos_db import list_combos
from janus.storage.cooldowns import get_active_cooldowns
from janus.storage.inventory_overview import (
    get_best_upstream_keys,
    get_credit_summary,
    get_inventory_summary,
    get_provider_cards,
    get_recent_activity,
    get_top_keys_per_provider,
)
from janus.storage.inventory_providers import list_inventory_providers
from janus.storage.key_access import parse_models_input
from janus.storage.routing_overview import get_routing_overview
from janus.storage.settings import (
    SAVER_SETTING_DEFAULTS,
    SERVER_SETTING_DEFAULTS,
    cooldowns_enabled,
    ensure_server_defaults,
    get_all_settings,
    get_reporting_timezone,
    request_logging_enabled,
    require_api_key_enabled,
    resolve_account_strategy,
    resolve_combo_fusion_hard_timeout_s,
    resolve_combo_fusion_judge,
    resolve_combo_fusion_min_panel,
    resolve_combo_fusion_straggler_grace_s,
    resolve_combo_sticky_limit,
    resolve_combo_strategy,
    resolve_gateway_rate_limit_rpm,
    resolve_reporting_timezone,
    resolve_request_log_retention,
    resolve_sticky_limit,
    sticky_client_key_routing_enabled,
)
from janus.storage.upstream_keys import (
    DEFAULT_PAGE_SIZE,
    SORT_COLUMNS,
    count_pending_upstream_keys,
    count_storage_encryption_state,
    count_upstream_keys_filtered,
    list_upstream_keys_page,
)
from janus.storage.usage import get_unpriced_models

router = APIRouter(dependencies=[Depends(require_dashboard_access)])

_SECTIONS = frozenset(
    {
        "overview",
        "usage",
        "analytics",
        "leaderboard",
        "request-logs",
        "inventory",
        "inventory-keys",
        "providers",
        "combos",
        "routing",
        "savers",
        "budgets",
        "keys",
        "tools",
        "pricing",
        "settings",
    }
)
_DIMENSIONS = frozenset({"model", "provider", "account", "client_key"})
_LEADERBOARD_SORTS = frozenset({"tokens", "cost", "requests"})
_KEY_STATUSES = frozenset({"active", "revoked", "all"})
_INVENTORY_STATUSES = frozenset(
    {
        "active",
        "invalid",
        "validation_paused",
        "pending_validation",
        "error",
        "daily_exhausted",
        "unidentified",
        "archived",
    }
)
_SAFE_SETTING_KEYS = frozenset(
    {
        *SAVER_SETTING_DEFAULTS,
        *SERVER_SETTING_DEFAULTS,
        "combo_strategy",
        "combo_sticky_limit",
    }
)
_SENSITIVE_JSON_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "dashboard_password_hash",
        "dashboard_session_secret",
        "key_hash",
        "key_value",
        "password",
        "password_hash",
        "provider_credentials",
        "refresh_token",
        "session_secret",
    }
)


def _invalid_query(name: str, detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"Invalid {name}: {detail}")


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_json_value(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_JSON_FIELDS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


async def _response(
    request: Request,
    db_path: Path,
    section: str,
    data: dict[str, Any],
    *,
    meta: dict[str, Any] | None = None,
) -> JSONResponse:
    alert_data = await collect_dashboard_alerts(db_path, request)
    response_meta: dict[str, Any] = {
        "alert_summary": alert_data["summary"],
        "alert_counts": alert_data["counts"],
    }
    if meta:
        response_meta.update(meta)
    encoded = jsonable_encoder(
        {
            "section": section,
            "alerts": alert_data["alerts"],
            "data": data,
            "meta": response_meta,
        }
    )
    return JSONResponse(
        _safe_json_value(encoded),
        headers={
            "Cache-Control": "private, no-store",
            "Expires": "0",
            "Pragma": "no-cache",
        },
    )


async def _usage_stats_data(db_path: Path, *, days: int) -> dict[str, Any]:
    lifetime = await _get_usage_stats_safe(db_path)
    try:
        period = await get_spend_summary(db_path, days=days)
    except Exception:
        period = {
            "total_cost": 0.0,
            "total_requests": lifetime["total_requests"],
            "total_input_tokens": lifetime["total_input_tokens"],
            "total_output_tokens": lifetime["total_output_tokens"],
            "total_cache_creation_tokens": 0,
            "total_cache_read_tokens": 0,
            "daily": [],
        }
    return {
        **period,
        "by_model": lifetime["by_model"],
        "period_days": days,
        "reporting_timezone": await get_reporting_timezone(db_path),
    }


async def _overview_data(request: Request, db_path: Path, *, days: int) -> dict[str, Any]:
    from janus.dashboard.live import get_bus
    from janus.storage.providers_db import list_providers

    stats = await _usage_stats_data(db_path, days=days)
    lifetime = await _get_usage_stats_safe(db_path)
    providers = await list_providers(db_path, enabled_only=True)
    keys = await list_keys(db_path)
    reporting_now = datetime.now(UTC)
    summary = await get_calendar_day_spend_summary(db_path, now=reporting_now)
    global_budget = await get_budget_status(db_path, key_id=None, now=reporting_now)
    cooldowns = await get_active_cooldowns(db_path)
    now = datetime.now(UTC).timestamp()
    cooled_accounts = {
        combined.rpartition("::")[0]
        for combined, (expires_at, _level) in cooldowns.items()
        if expires_at > now
    }
    live = get_bus().snapshot()
    registry = request.app.state.registry
    return {
        "stats": stats,
        "provider_count": len(providers),
        "combos": registry.combos,
        "today_cost": summary["total_cost"],
        "reporting_timezone": summary["reporting_timezone"],
        "global_budget": global_budget,
        "base_url": _api_v1_base_url(request),
        "live": live,
        "cooldown_count": len(cooled_accounts),
        "setup_checklist": {
            "has_providers": bool(providers),
            "has_keys": any(key.get("is_active") for key in keys),
            "has_requests": lifetime["total_requests"] > 0,
        },
    }


async def _analytics_data(
    request: Request, db_path: Path, *, days: int, dimension: str
) -> dict[str, Any]:
    if dimension not in _DIMENSIONS:
        raise _invalid_query("dimension", "expected model, provider, account, or client_key")
    summary = await get_spend_summary(db_path, days=days)
    breakdown = await get_breakdown(db_path, dimension=cast(Dimension, dimension), days=days)
    success = await get_success_rate(db_path, days=days)
    flow = await get_flow(db_path, days=days)
    raw_unpriced = await get_unpriced_models(db_path, days=days)
    registry = request.app.state.pricing_registry
    unpriced = [row for row in raw_unpriced if registry.get(str(row["model"])) is None]
    return {
        "summary": summary,
        "breakdown": breakdown,
        "success": success,
        "flow": flow,
        "unpriced_models": unpriced,
        "unpriced_model_ids": [str(row["model"]) for row in unpriced],
    }


async def _request_logs_data(
    db_path: Path, *, limit: int, offset: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = await get_all_settings(db_path)
    context = await _request_logs_context(db_path, limit=limit, offset=offset)
    return (
        {
            "logs": context["logs"],
            "logging_enabled": request_logging_enabled(settings),
            "retention_max": context["retention_max"],
        },
        {
            "pagination": {
                "total": context["total"],
                "limit": context["limit"],
                "offset": context["offset"],
                "page": context["page"],
                "total_pages": context["total_pages"],
            }
        },
    )


async def _inventory_data(db_path: Path) -> dict[str, Any]:
    from janus.inventory.key_encryption import encryption_enabled
    from janus.storage.providers_db import count_provider_encryption_state

    provider_cards = [
        card for card in await get_provider_cards(db_path) if int(card.get("total_keys") or 0) > 0
    ]
    return {
        "summary": await get_inventory_summary(db_path),
        "provider_cards": provider_cards,
        "recent_activity": await get_recent_activity(db_path),
        "credit_summary": await get_credit_summary(db_path),
        "best_keys": await get_best_upstream_keys(db_path),
        "top_keys": await get_top_keys_per_provider(db_path),
        "encryption": await count_storage_encryption_state(db_path),
        "provider_encryption": await count_provider_encryption_state(db_path),
        "encryption_enabled": encryption_enabled(),
    }


async def _inventory_keys_data(
    db_path: Path,
    *,
    provider_id: str,
    status: str,
    search: str,
    sort: str,
    direction: str,
    limit: int,
    offset: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if status and status not in _INVENTORY_STATUSES:
        raise _invalid_query("status", "unknown inventory status")
    if sort not in SORT_COLUMNS:
        raise _invalid_query("sort", f"expected one of {', '.join(sorted(SORT_COLUMNS))}")
    providers = await list_inventory_providers(db_path, active_only=True)
    provider_ids = {str(provider["id"]) for provider in providers}
    if provider_id and provider_id not in provider_ids:
        raise _invalid_query("provider_id", "unknown inventory provider")
    include_archived = status == "archived"
    total = await count_upstream_keys_filtered(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
        include_archived=include_archived,
    )
    if total and offset >= total:
        offset = max(0, ((total - 1) // limit) * limit)
    keys = await list_upstream_keys_page(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
        masked=True,
        include_archived=include_archived,
    )
    for key in keys:
        key.pop("key_hash", None)
        key.pop("metadata", None)
    return (
        {
            "keys": keys,
            "has_pending": await count_pending_upstream_keys(db_path) > 0,
            "filters": {
                "providers": providers,
                "statuses": sorted(_INVENTORY_STATUSES),
                "sorts": sorted(SORT_COLUMNS),
            },
        },
        {
            "query": {
                "provider_id": provider_id,
                "status": status,
                "search": search,
                "sort": sort,
                "direction": direction,
            },
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "page": (offset // limit) + 1,
                "total_pages": max(1, (total + limit - 1) // limit),
            },
        },
    )


def _without_provider_secrets(provider: dict[str, Any]) -> dict[str, Any]:
    safe = dict(provider)
    safe.pop("api_key", None)
    safe["has_api_key"] = bool(provider.get("api_key"))
    return safe


async def _providers_data(db_path: Path) -> dict[str, Any]:
    from janus.dashboard.catalog import get_catalog, get_provider_logo_map

    providers = [
        _without_provider_secrets(provider) for provider in await _enrich_providers(db_path)
    ]
    quota_warnings = [
        provider
        for provider in providers
        if provider.get("is_enabled")
        and provider.get("quota")
        and provider["quota"]["status"] in ("warning", "exhausted")
    ]
    return {
        "providers": providers,
        "catalog": get_catalog(),
        "logo_map": get_provider_logo_map(),
        "quota_warnings": quota_warnings,
    }


async def _combos_data(request: Request, db_path: Path) -> dict[str, Any]:
    combos: list[dict[str, Any]] = []
    for raw in await list_combos(db_path):
        combo = dict(raw)
        combo["models_list"] = json.loads(combo["models"]) if combo["models"] else []
        combos.append(combo)
    return {"combos": combos, "wired_providers": _wired_providers(request)}


async def _routing_data(request: Request, db_path: Path) -> dict[str, Any]:
    settings = await get_all_settings(db_path)
    return {
        "overview": await get_routing_overview(db_path),
        "live": request.app.state.fallback_handler.routing_snapshot(),
        "settings": {
            "cooldowns_enabled": cooldowns_enabled(settings),
            "account_strategy": resolve_account_strategy(settings),
            "sticky_client_key_routing_enabled": sticky_client_key_routing_enabled(settings),
            "sticky_limit": resolve_sticky_limit(settings),
            "combo_strategy": resolve_combo_strategy(settings),
        },
    }


async def _budgets_data(db_path: Path) -> dict[str, Any]:
    try:
        budgets, keys = await _build_budget_statuses(db_path)
    except Exception:
        budgets, keys = [], []
    return {
        "budgets": budgets,
        "keys": keys,
        "reporting_timezone": await get_reporting_timezone(db_path),
    }


async def _keys_data(db_path: Path, *, status: str) -> dict[str, Any]:
    if status not in _KEY_STATUSES:
        raise _invalid_query("status", "expected active, revoked, or all")
    keys = await list_keys(db_path)
    active = [key for key in keys if key["is_active"]]
    revoked = [key for key in keys if not key["is_active"]]
    shown = keys if status == "all" else active if status == "active" else revoked
    for key in shown:
        key["budget"] = await get_budget_status(db_path, key_id=int(key["id"]))
    return {
        "keys": shown,
        "status": status,
        "counts": {"active": len(active), "revoked": len(revoked), "all": len(keys)},
    }


async def _settings_data(db_path: Path) -> dict[str, Any]:
    await ensure_server_defaults(db_path)
    settings = await get_all_settings(db_path)
    safe_values = {key: settings[key] for key in _SAFE_SETTING_KEYS if key in settings}
    return {
        "values": safe_values,
        "dashboard_access": {
            "mode": "api_key",
            "keys_url": "/dashboard/ui/keys",
            "localhost_requires_auth": True,
        },
        "status": {
            "require_api_key_enabled": require_api_key_enabled(settings),
            "cooldowns_enabled": cooldowns_enabled(settings),
            "sticky_client_key_routing_enabled": sticky_client_key_routing_enabled(settings),
            "request_logging_enabled": request_logging_enabled(settings),
            "request_log_retention": resolve_request_log_retention(settings),
            "account_strategy": resolve_account_strategy(settings),
            "sticky_limit": resolve_sticky_limit(settings),
            "gateway_rate_limit_rpm": resolve_gateway_rate_limit_rpm(settings),
            "reporting_timezone": resolve_reporting_timezone(settings),
            "combo_strategy": resolve_combo_strategy(settings),
            "combo_sticky_limit": resolve_combo_sticky_limit(settings),
            "combo_fusion_judge": resolve_combo_fusion_judge(settings),
            "combo_fusion_min_panel": resolve_combo_fusion_min_panel(settings),
            "combo_fusion_straggler_grace_s": resolve_combo_fusion_straggler_grace_s(settings),
            "combo_fusion_hard_timeout_s": resolve_combo_fusion_hard_timeout_s(settings),
        },
        "export": {
            "available": True,
            "method": "GET",
            "url": "/dashboard/api/export",
            "contains_credentials": True,
        },
    }


@router.post("/api/v2/keys")
async def create_dashboard_api_key(
    request: Request,
    name: str = Form(...),
    can_login: str = Form(""),
    login_field: str = Form(""),
    allowed_models: str = Form(""),
    daily_budget: str = Form(""),
) -> JSONResponse:
    db_path = await _ensure_db(request)
    key_name = name.strip()
    if not key_name:
        raise HTTPException(status_code=422, detail="API key name is required")
    if len(key_name) > 200:
        raise HTTPException(status_code=422, detail="API key name must be 200 characters or fewer")

    budget_limit: float | None = None
    budget_text = daily_budget.strip()
    if budget_text:
        try:
            budget_limit = float(budget_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Daily budget must be a number") from exc
        if not math.isfinite(budget_limit) or budget_limit < 0:
            raise HTTPException(
                status_code=422,
                detail="Daily budget must be a finite non-negative number",
            )

    login_ok = can_login.lower() in {"on", "1", "true", "yes"} if login_field else True
    plaintext, record = await create_key(
        db_path,
        key_name,
        can_login=login_ok,
        allowed_models=parse_models_input(allowed_models),
    )
    if budget_limit is not None and budget_limit > 0:
        await create_or_update_budget(
            db_path,
            key_id=int(record["id"]),
            daily_limit=budget_limit,
        )

    return JSONResponse(
        {"api_key": plaintext, "key": record},
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v2/state/{section}")
async def get_dashboard_state(
    request: Request,
    section: str,
    days: int = Query(30, ge=1, le=365),
    dimension: str = Query("model"),
    sort: str = Query(""),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query(""),
    provider_id: str = Query("", max_length=100),
    search: str = Query("", max_length=200),
    direction: Literal["asc", "desc"] = Query("desc", alias="dir"),
) -> JSONResponse:
    if section not in _SECTIONS:
        raise HTTPException(status_code=404, detail="Dashboard section not found")
    db_path = await _ensure_db(request)

    if section == "overview":
        return await _response(
            request,
            db_path,
            section,
            await _overview_data(request, db_path, days=days),
            meta={"query": {"days": days}},
        )
    if section == "usage":
        from janus.dashboard.live import get_bus

        return await _response(
            request,
            db_path,
            section,
            {"stats": await _usage_stats_data(db_path, days=days), "live": get_bus().snapshot()},
            meta={"query": {"days": days}},
        )
    if section == "analytics":
        return await _response(
            request,
            db_path,
            section,
            await _analytics_data(request, db_path, days=days, dimension=dimension),
            meta={"query": {"days": days, "dimension": dimension}},
        )
    if section == "leaderboard":
        board_sort = sort or "tokens"
        if board_sort not in _LEADERBOARD_SORTS:
            raise _invalid_query("sort", "expected tokens, cost, or requests")
        board = await get_leaderboard(db_path, days=days, sort_by=board_sort, limit=limit)
        return await _response(
            request,
            db_path,
            section,
            {"leaderboard": board},
            meta={"query": {"days": days, "sort": board_sort, "limit": limit}},
        )
    if section == "request-logs":
        data, meta = await _request_logs_data(db_path, limit=limit, offset=offset)
        return await _response(request, db_path, section, data, meta=meta)
    if section == "inventory":
        return await _response(request, db_path, section, await _inventory_data(db_path))
    if section == "inventory-keys":
        inventory_sort = sort or "credits"
        data, meta = await _inventory_keys_data(
            db_path,
            provider_id=provider_id,
            status=status,
            search=search,
            sort=inventory_sort,
            direction=direction,
            limit=limit,
            offset=offset,
        )
        return await _response(request, db_path, section, data, meta=meta)
    if section == "providers":
        return await _response(request, db_path, section, await _providers_data(db_path))
    if section == "combos":
        return await _response(request, db_path, section, await _combos_data(request, db_path))
    if section == "routing":
        return await _response(request, db_path, section, await _routing_data(request, db_path))
    if section == "savers":
        saver_context = await _savers_context(request, db_path)
        saver_context.pop("request", None)
        return await _response(request, db_path, section, saver_context)
    if section == "budgets":
        return await _response(request, db_path, section, await _budgets_data(db_path))
    if section == "keys":
        return await _response(
            request, db_path, section, await _keys_data(db_path, status=status or "active")
        )
    if section == "tools":
        from janus.api.auth import is_require_api_key_enabled

        return await _response(
            request,
            db_path,
            section,
            {
                "base_url": _api_v1_base_url(request),
                "require_api_key": await is_require_api_key_enabled(request),
            },
        )
    if section == "pricing":
        pricing = await _pricing_page_context(request, db_path)
        pricing.pop("request", None)
        return await _response(request, db_path, section, pricing)
    return await _response(request, db_path, section, await _settings_data(db_path))
