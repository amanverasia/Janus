from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from janus.storage.analytics import (
    Dimension,
    get_breakdown,
    get_spend_summary,
    get_success_rate,
)
from janus.storage.api_keys import create_key, list_keys, revoke_key
from janus.storage.budgets import (
    create_or_update_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
)
from janus.storage.database import init_db
from janus.storage.usage import get_usage_stats

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def _ensure_db(request: Request) -> Path:
    db_path = Path(request.app.state.db_path)
    if not getattr(request.app.state, "_dashboard_db_ready", False):
        await init_db(db_path)
        request.app.state._dashboard_db_ready = True
    return db_path


async def _get_usage_stats_safe(db_path: Path) -> dict[str, Any]:
    try:
        return await get_usage_stats(db_path)
    except Exception:
        return {
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "by_model": [],
        }


@router.get("", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    stats = await _get_usage_stats_safe(db_path)
    registry = request.app.state.registry
    provider_count = len(registry.providers)
    today_cost = 0.0
    global_budget = None
    try:
        summary = await get_spend_summary(db_path, days=1)
        today_cost = summary["total_cost"]
        global_budget = await get_budget_status(db_path, key_id=None)
    except Exception:
        pass
    context: dict[str, Any] = {
        "request": request,
        "stats": stats,
        "provider_count": provider_count,
        "combos": registry.combos,
        "today_cost": today_cost,
        "global_budget": global_budget,
    }
    return _templates.TemplateResponse(request, "overview.html", context)


@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request) -> HTMLResponse:
    registry = request.app.state.registry
    context: dict[str, Any] = {
        "request": request,
        "providers": registry.providers,
    }
    return _templates.TemplateResponse(request, "providers.html", context)


@router.get("/combos", response_class=HTMLResponse)
async def combos_page(request: Request) -> HTMLResponse:
    registry = request.app.state.registry
    context: dict[str, Any] = {
        "request": request,
        "combos": registry.combos,
    }
    return _templates.TemplateResponse(request, "combos.html", context)


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    keys = await list_keys(db_path)
    context: dict[str, Any] = {
        "request": request,
        "keys": keys,
        "new_key": None,
    }
    return _templates.TemplateResponse(request, "keys.html", context)


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    stats = await _get_usage_stats_safe(db_path)
    context: dict[str, Any] = {
        "request": request,
        "stats": stats,
    }
    return _templates.TemplateResponse(request, "usage.html", context)


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    days: int = 30,
    dimension: str = "model",
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    valid_dims = ("model", "provider", "account", "client_key")
    if dimension not in valid_dims:
        dimension = "model"
    try:
        summary = await get_spend_summary(db_path, days=days)
        breakdown = await get_breakdown(db_path, dimension=cast(Dimension, dimension), days=days)
        success = await get_success_rate(db_path, days=days)
    except Exception:
        summary = {
            "total_cost": 0,
            "total_requests": 0,
            "daily": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        breakdown = []
        success = {"success_2xx": 0, "client_4xx": 0, "server_5xx": 0, "total": 0}
    context: dict[str, Any] = {
        "request": request,
        "summary": summary,
        "breakdown": breakdown,
        "success": success,
        "days": days,
        "dimension": dimension,
    }
    return _templates.TemplateResponse(request, "analytics.html", context)


@router.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    try:
        budgets = await get_budgets(db_path)
        keys = await list_keys(db_path)
        budget_statuses: list[dict[str, Any]] = []
        for b in budgets:
            status = await get_budget_status(db_path, key_id=b["key_id"])
            key_name = "Global"
            if b["key_id"] is not None:
                key_name = next(
                    (k["name"] for k in keys if k["id"] == b["key_id"]),
                    f"Key #{b['key_id']}",
                )
            budget_statuses.append({**b, "status": status, "key_name": key_name})
    except Exception:
        budget_statuses = []
        keys = []
    context: dict[str, Any] = {
        "request": request,
        "budgets": budget_statuses,
        "keys": keys,
    }
    return _templates.TemplateResponse(request, "budgets.html", context)


@router.post("/api/budgets", response_class=HTMLResponse)
async def create_budget(
    request: Request,
    key_select: str = Form(...),
    daily_limit: float = Form(...),
    warn_pct: float = Form(80),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    key_id: int | None = None
    if key_select != "global":
        key_id = int(key_select)
    await create_or_update_budget(
        db_path, key_id=key_id, daily_limit=daily_limit, warn_pct=warn_pct
    )
    return await _budgets_partial(request, db_path)


@router.delete("/api/budgets/{budget_id}", response_class=HTMLResponse)
async def delete_budget_endpoint(request: Request, budget_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await delete_budget(db_path, budget_id)
    return await _budgets_partial(request, db_path)


async def _budgets_partial(request: Request, db_path: Path) -> HTMLResponse:
    budgets = await get_budgets(db_path)
    keys = await list_keys(db_path)
    budget_statuses: list[dict[str, Any]] = []
    for b in budgets:
        status = await get_budget_status(db_path, key_id=b["key_id"])
        key_name = "Global"
        if b["key_id"] is not None:
            key_name = next(
                (k["name"] for k in keys if k["id"] == b["key_id"]),
                f"Key #{b['key_id']}",
            )
        budget_statuses.append({**b, "status": status, "key_name": key_name})
    context: dict[str, Any] = {
        "request": request,
        "budgets": budget_statuses,
        "keys": keys,
    }
    return _templates.TemplateResponse(request, "budgets_partial.html", context)


@router.post("/api/keys", response_class=HTMLResponse)
async def create_api_key(request: Request, name: str = Form(...)) -> HTMLResponse:
    db_path = await _ensure_db(request)
    new_key, _ = await create_key(db_path, name)
    keys = await list_keys(db_path)
    context: dict[str, Any] = {
        "request": request,
        "keys": keys,
        "new_key": new_key,
    }
    return _templates.TemplateResponse(request, "keys_partial.html", context)


@router.delete("/api/keys/{key_id}", response_class=HTMLResponse)
async def revoke_api_key(request: Request, key_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await revoke_key(db_path, key_id)
    keys = await list_keys(db_path)
    context: dict[str, Any] = {
        "request": request,
        "keys": keys,
        "new_key": None,
    }
    return _templates.TemplateResponse(request, "keys_partial.html", context)
