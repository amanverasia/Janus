from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from janus.storage.api_keys import create_key, list_keys, revoke_key
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
    context: dict[str, Any] = {
        "request": request,
        "stats": stats,
        "provider_count": provider_count,
        "combos": registry.combos,
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
