from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from janus.api.auth import authenticate_api_key
from janus.dashboard.auth import require_dashboard_access
from janus.dashboard.catalog import get_catalog
from janus.providers.drivers import supported_api_types
from janus.storage.api_keys import list_keys, revoke_key, update_key
from janus.storage.budgets import (
    create_or_update_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
)
from janus.storage.database import init_db
from janus.storage.key_access import parse_models_input
from janus.storage.settings import (
    VALID_COMBO_STRATEGIES,
    get_setting,
    validate_reporting_timezone,
)
from janus.storage.usage import get_unpriced_models, get_usage_stats

router = APIRouter(
    dependencies=[Depends(require_dashboard_access)],
    include_in_schema=False,
)
dashboard_page_redirect_router = APIRouter(
    dependencies=[Depends(require_dashboard_access)],
    include_in_schema=False,
)
logger = logging.getLogger(__name__)

_DASHBOARD_UI_ROOT = "/dashboard/ui"
_DASHBOARD_PAGE_TARGETS = {
    "/dashboard": _DASHBOARD_UI_ROOT,
    "/dashboard/analytics": f"{_DASHBOARD_UI_ROOT}/analytics",
    "/dashboard/budgets": f"{_DASHBOARD_UI_ROOT}/budgets",
    "/dashboard/combos": f"{_DASHBOARD_UI_ROOT}/combos",
    "/dashboard/inventory": f"{_DASHBOARD_UI_ROOT}/inventory",
    "/dashboard/inventory/add": f"{_DASHBOARD_UI_ROOT}/inventory/add",
    "/dashboard/inventory/import": f"{_DASHBOARD_UI_ROOT}/inventory/import",
    "/dashboard/inventory/keys": f"{_DASHBOARD_UI_ROOT}/inventory/keys",
    "/dashboard/keys": f"{_DASHBOARD_UI_ROOT}/keys",
    "/dashboard/leaderboard": f"{_DASHBOARD_UI_ROOT}/leaderboard",
    "/dashboard/pricing": f"{_DASHBOARD_UI_ROOT}/pricing",
    "/dashboard/providers": f"{_DASHBOARD_UI_ROOT}/providers",
    "/dashboard/request-logs": f"{_DASHBOARD_UI_ROOT}/request-logs",
    "/dashboard/routing": f"{_DASHBOARD_UI_ROOT}/routing",
    "/dashboard/savers": f"{_DASHBOARD_UI_ROOT}/savers",
    "/dashboard/settings": f"{_DASHBOARD_UI_ROOT}/settings",
    "/dashboard/tools": f"{_DASHBOARD_UI_ROOT}/tools",
    "/dashboard/usage": f"{_DASHBOARD_UI_ROOT}/usage",
}


def _canonical_dashboard_next(value: str) -> str:
    path, separator, query = value.partition("?")
    if path != "/dashboard" and not path.startswith("/dashboard/"):
        return _DASHBOARD_UI_ROOT
    normalized_path = path.rstrip("/") or "/"
    target = _DASHBOARD_PAGE_TARGETS.get(normalized_path, path)
    return f"{target}{separator}{query}" if separator else target


def _dashboard_page_redirect(request: Request) -> RedirectResponse:
    target = _DASHBOARD_PAGE_TARGETS[request.url.path.rstrip("/")]
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=308)


for _dashboard_path in _DASHBOARD_PAGE_TARGETS:
    _router_path = _dashboard_path.removeprefix("/dashboard")
    for _route_variant in (_router_path, f"{_router_path}/"):
        dashboard_page_redirect_router.add_api_route(
            _route_variant,
            _dashboard_page_redirect,
            methods=["GET"],
        )

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _api_v1_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/v1"


def _reject_unsafe_url(
    base_url: str, *, allow_private_network: bool = False
) -> JSONResponse | None:
    """Return a 400 JSONResponse if base_url is not a public http(s) address, else None.

    Guards dashboard endpoints that send the user's API key to an arbitrary URL
    against scheme abuse and SSRF to internal/private addresses.
    """
    import ipaddress
    import socket

    try:
        parsed = httpx.URL(base_url)
    except Exception:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    if parsed.scheme not in ("http", "https"):
        return JSONResponse({"error": "Only http/https URLs are allowed"}, status_code=400)
    try:
        hostname = parsed.host
        if hostname and not allow_private_network:
            for _family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return JSONResponse(
                        {"error": "URLs pointing to internal/private addresses are not allowed"},
                        status_code=400,
                    )
    except (socket.gaierror, ValueError):
        pass
    return None


async def _ensure_db(request: Request) -> Path:
    db_path = Path(request.app.state.db_path)
    if not getattr(request.app.state, "_dashboard_db_ready", False):
        await init_db(db_path)
        from janus.storage.database import seed_from_config

        await seed_from_config(db_path, request.app.state.config)

        from janus.storage.settings import ensure_server_defaults

        await ensure_server_defaults(db_path)

        from janus.dashboard.reload import (
            reload_combos,
            reload_pricing,
            reload_providers,
            reload_savers,
        )

        await reload_providers(request.app)
        await reload_combos(request.app)
        await reload_savers(request.app)
        await reload_pricing(request.app)
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = _DASHBOARD_UI_ROOT) -> HTMLResponse:
    next = _canonical_dashboard_next(next)
    await _ensure_db(request)
    context: dict[str, Any] = {
        "request": request,
        "next": next,
        "error": None,
    }
    return _templates.TemplateResponse(request, "login.html", context)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    api_key: str = Form(""),
    next: str = Form(_DASHBOARD_UI_ROOT),
) -> Response:
    next = _canonical_dashboard_next(next)
    await _ensure_db(request)

    if not api_key.strip():
        context: dict[str, Any] = {
            "request": request,
            "next": next,
            "error": "API key is required",
        }
        return _templates.TemplateResponse(request, "login.html", context, status_code=401)

    if not await authenticate_api_key(request, api_key.strip()):
        context = {
            "request": request,
            "next": next,
            "error": "Invalid API key",
        }
        return _templates.TemplateResponse(request, "login.html", context, status_code=401)
    from janus.api.auth import key_can_login

    if not key_can_login(request):
        context = {
            "request": request,
            "next": next,
            "error": "This API key cannot access the dashboard",
        }
        return _templates.TemplateResponse(request, "login.html", context, status_code=401)
    response = RedirectResponse(url=next, status_code=303)
    response.set_cookie(
        "janus_dashboard_key",
        api_key.strip(),
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/dashboard/login", status_code=303)
    response.delete_cookie("janus_dashboard_key")
    response.delete_cookie("janus_dashboard_session")
    return response


def _wired_providers(request: Request) -> list[dict[str, Any]]:
    """Prefixes with credentialed provider configs, and the models they expose.

    The registry only holds providers built from stored credentials, so this
    is exactly the set of routable `prefix/model` targets. Models blocked by a
    provider's allowlist are dropped; a prefix with several accounts shows the
    union of their permitted models.
    """
    from janus.providers.registry import ProviderRegistry, model_allowed

    registry: ProviderRegistry = request.app.state.registry
    wired: list[dict[str, Any]] = []
    for prefix in sorted(registry.providers):
        models: list[str] = []
        seen: set[str] = set()
        account_count = len(registry.providers[prefix])
        for config in registry.providers[prefix]:
            for model in config.models:
                if model in seen or not model_allowed(model, config.allowed_models):
                    continue
                seen.add(model)
                models.append(model)
        wired.append({"prefix": prefix, "models": models, "accounts": account_count})
    return wired


@router.post("/api/routing/cooldowns/clear")
async def api_clear_cooldowns(request: Request) -> JSONResponse:
    await _ensure_db(request)
    handler = request.app.state.fallback_handler
    n = await handler.clear_all_cooldowns()
    return JSONResponse({"ok": True, "cleared": n})


@router.get("/api/usage/snapshot")
async def usage_snapshot(_request: Request) -> JSONResponse:
    from janus.dashboard.live import get_bus

    snap = get_bus().snapshot()
    return JSONResponse({"inflight": snap["inflight"], "recent": snap["recent"][-5:]})


@router.get("/api/usage/live")
async def usage_live_stream(request: Request) -> StreamingResponse:
    """SSE feed for the Usage tab's live activity view.

    Emits a `snapshot` event on connect (in-flight count + recent-request
    ring), then pushes `request` events as usage is recorded and `inflight`
    gauge updates as gateway requests start/finish. A comment ping every 25s
    keeps proxies from closing the idle stream.
    """
    from janus.dashboard.live import get_bus

    bus = get_bus()

    async def _events() -> AsyncIterator[bytes]:
        q = bus.subscribe()
        try:
            yield f"data: {json.dumps(bus.snapshot())}\n\n".encode()
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                except TimeoutError:
                    yield b": ping\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n".encode()
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _clamp_page_size(limit: int) -> int:
    return max(1, min(limit, 200))


async def _request_logs_context(
    db_path: Path, *, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    from janus.storage.request_logs import count_request_logs, list_request_logs
    from janus.storage.settings import get_all_settings, resolve_request_log_retention

    limit = _clamp_page_size(limit)
    total = await count_request_logs(db_path)
    if offset < 0:
        offset = 0
    if total and offset >= total:
        offset = max(0, ((total - 1) // limit) * limit)
    logs = await list_request_logs(db_path, limit=limit, offset=offset)
    settings = await get_all_settings(db_path)
    return {
        "logs": logs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "page": (offset // limit) + 1 if limit else 1,
        "total_pages": max(1, (total + limit - 1) // limit) if limit else 1,
        "retention_max": resolve_request_log_retention(settings),
    }


@router.get("/api/request-logs/export")
async def api_export_request_logs(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.request_logs import export_request_logs

    logs = await export_request_logs(db_path)
    return JSONResponse(
        content=logs,
        headers={"Content-Disposition": "attachment; filename=janus-request-logs.json"},
    )


@router.get("/api/request-logs/{log_id}")
async def api_get_request_log(request: Request, log_id: int) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.request_logs import get_request_log

    log = await get_request_log(db_path, log_id)
    if log is None:
        return JSONResponse(content={"error": "not found"}, status_code=404)
    return JSONResponse(content=log)


@router.delete("/api/request-logs")
async def api_clear_request_logs(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.request_logs import clear_request_logs

    await clear_request_logs(db_path)
    return JSONResponse({"ok": True})


@router.post("/api/budgets")
async def create_budget(
    request: Request,
    key_select: str = Form(""),
    daily_limit: str = Form(""),
    warn_pct: str = Form("80"),
) -> Response:
    db_path = await _ensure_db(request)
    selected = key_select.strip()
    key_id: int | None = None
    if selected != "global":
        if not selected.isascii() or not selected.isdigit() or int(selected) <= 0:
            return _budget_validation_error("Select a valid budget scope.")
        key_id = int(selected)
        keys = await list_keys(db_path)
        if not any(key["id"] == key_id for key in keys):
            return _budget_validation_error("The selected API key does not exist.")
    try:
        parsed_daily_limit = float(daily_limit)
    except ValueError:
        return _budget_validation_error("Daily limit must be a number greater than zero.")
    if not math.isfinite(parsed_daily_limit) or parsed_daily_limit <= 0:
        return _budget_validation_error("Daily limit must be a number greater than zero.")
    try:
        parsed_warn_pct = float(warn_pct)
    except ValueError:
        return _budget_validation_error("Warning percentage must be between 1 and 100.")
    if not math.isfinite(parsed_warn_pct) or not 1 <= parsed_warn_pct <= 100:
        return _budget_validation_error("Warning percentage must be between 1 and 100.")
    await create_or_update_budget(
        db_path,
        key_id=key_id,
        daily_limit=parsed_daily_limit,
        warn_pct=parsed_warn_pct,
    )
    return JSONResponse({"ok": True})


def _budget_validation_error(message: str) -> HTMLResponse:
    return HTMLResponse(content=message, status_code=422)


@router.delete("/api/budgets/{budget_id}")
async def delete_budget_endpoint(request: Request, budget_id: int) -> JSONResponse:
    db_path = await _ensure_db(request)
    await delete_budget(db_path, budget_id)
    return JSONResponse({"ok": True})


async def _build_budget_statuses(
    db_path: Path,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reporting_now = now or datetime.now(UTC)
    budgets = await get_budgets(db_path)
    keys = await list_keys(db_path)
    budget_statuses: list[dict[str, Any]] = []
    for b in budgets:
        status = await get_budget_status(db_path, key_id=b["key_id"], now=reporting_now)
        key_name = "Global"
        if b["key_id"] is not None:
            key_name = next(
                (k["name"] for k in keys if k["id"] == b["key_id"]),
                f"Key #{b['key_id']}",
            )
        budget_statuses.append({**b, "status": status, "key_name": key_name})
    return budget_statuses, keys


@router.post("/api/keys/{key_id}")
async def update_api_key(
    request: Request,
    key_id: int,
    name: str = Form(""),
    can_login: str = Form(""),
    login_field: str = Form(""),
    allowed_models: str = Form(""),
    models_field: str = Form(""),
    clear_models: str = Form(""),
    daily_budget: str = Form(""),
) -> JSONResponse:
    db_path = await _ensure_db(request)
    kwargs: dict[str, Any] = {}
    if name.strip():
        kwargs["name"] = name.strip()
    if login_field:
        kwargs["can_login"] = can_login.lower() in {"on", "1", "true", "yes"}
    if clear_models.lower() in {"on", "1", "true", "yes"}:
        kwargs["allowed_models"] = None
    elif models_field:
        kwargs["allowed_models"] = parse_models_input(allowed_models)
    if kwargs:
        await update_key(db_path, key_id, **kwargs)
    budget_text = daily_budget.strip()
    if budget_text:
        try:
            limit = float(budget_text)
        except ValueError:
            limit = None
        if limit is not None and limit > 0:
            from janus.storage.budgets import create_or_update_budget

            await create_or_update_budget(db_path, key_id=key_id, daily_limit=limit)
    return JSONResponse({"ok": True})


@router.delete("/api/keys/{key_id}")
async def revoke_api_key(request: Request, key_id: int) -> JSONResponse:
    db_path = await _ensure_db(request)
    await revoke_key(db_path, key_id)
    return JSONResponse({"ok": True})


# ---- Provider CRUD ----


_SUPPORTED_PROVIDER_API_TYPES = supported_api_types()


def _provider_api_type_error(api_type: str) -> HTMLResponse | None:
    if not api_type:
        return HTMLResponse(content="Missing required field: api_type", status_code=400)
    if api_type not in _SUPPORTED_PROVIDER_API_TYPES:
        return HTMLResponse(
            content="Unsupported API type. Choose a supported Janus executor.",
            status_code=422,
        )
    return None


def _parse_quota_params(params: dict[str, list[str]]) -> dict[str, Any]:
    from janus.storage.quotas import QUOTA_WINDOWS

    window = params.get("quota_window", [""])[0].strip()
    limit_str = params.get("quota_limit", [""])[0].strip()
    metric = params.get("quota_metric", ["requests"])[0].strip()
    limit = int(limit_str) if limit_str.isdigit() and int(limit_str) > 0 else None
    if window not in QUOTA_WINDOWS or limit is None:
        return {"quota_window": None, "quota_limit": None, "quota_metric": "requests"}
    return {
        "quota_window": window,
        "quota_limit": limit,
        "quota_metric": metric if metric in ("requests", "tokens") else "requests",
    }


_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _parse_provider_models(value: str) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,\n]", value):
        model = raw.strip()
        if not model or model in seen:
            continue
        if len(model) > 300 or any(ord(char) < 32 for char in model):
            raise ValueError("Model IDs must be 300 characters or fewer")
        seen.add(model)
        models.append(model)
    return models


def _form_bool(params: dict[str, list[str]], name: str, default: bool) -> bool:
    if name not in params:
        return default
    return params[name][0].strip().lower() in {"1", "on", "true", "yes"}


def _provider_form_data(
    params: dict[str, list[str]],
    *,
    provider_id: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = get_catalog()
    requested_catalog_id = params.get("catalog_id", [""])[0].strip()
    catalog_id = requested_catalog_id or str((existing or {}).get("catalog_id") or "")
    if not catalog_id and provider_id in catalog:
        catalog_id = provider_id
    preset = catalog.get(catalog_id) if catalog_id else None
    if requested_catalog_id and preset is None:
        raise ValueError("Unknown provider preset")

    prefix = params.get("prefix", [str((preset or {}).get("prefix") or "")])[0].strip()
    api_type = params.get("api_type", [str((preset or {}).get("api_type") or "")])[0].strip()
    base_url = params.get("base_url", [str((preset or {}).get("base_url") or "")])[0].strip()
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise ValueError("Provider ID must use letters, numbers, dots, dashes, or underscores")
    if not _PROVIDER_ID_PATTERN.fullmatch(prefix):
        raise ValueError("Prefix must use letters, numbers, dots, dashes, or underscores")
    url_optional = api_type in {"mimo_free", "opencode_free"}
    if not base_url and not url_optional:
        raise ValueError("Base URL is required")
    if base_url:
        try:
            parsed_url = httpx.URL(base_url)
        except Exception as exc:
            raise ValueError("Base URL must be a valid HTTP(S) URL") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("Base URL must be a valid HTTP(S) URL")
        if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise ValueError("Base URL must not include credentials, a query, or a fragment")
    api_type_error = _provider_api_type_error(api_type)
    if api_type_error is not None:
        raise ValueError("Unsupported API type")

    models_default = ",".join((preset or {}).get("default_models") or [])
    selected_default = ""
    if existing and existing.get("selected_models"):
        selected_default = ",".join(json.loads(existing["selected_models"]))
    allowed_default = ""
    if existing and existing.get("allowed_models"):
        allowed_default = ",".join(json.loads(existing["allowed_models"]))
    models = _parse_provider_models(params.get("models", [models_default])[0])
    selected_models = _parse_provider_models(params.get("selected_models", [selected_default])[0])
    allowed_models = _parse_provider_models(params.get("allowed_models", [allowed_default])[0])
    default_model = params.get("default_model", [str((preset or {}).get("default_model") or "")])[
        0
    ].strip()
    if default_model and default_model not in models:
        models.append(default_model)
    live_default = bool((preset or {}).get("live_models", True))
    if existing is not None:
        live_default = bool(existing.get("live_models", live_default))
    transports = (preset or {}).get("transports")
    if transports is None and existing:
        transports = existing.get("transports")
        if isinstance(transports, str) and transports:
            try:
                transports = json.loads(transports)
            except json.JSONDecodeError:
                transports = None
    return {
        "catalog_id": catalog_id or None,
        "prefix": prefix,
        "api_type": api_type,
        "base_url": base_url.rstrip("/"),
        "models": models,
        "selected_models": selected_models,
        "allowed_models": allowed_models,
        "default_model": default_model or None,
        "live_models": _form_bool(params, "live_models", live_default),
        "transports": transports,
        **_parse_quota_params(params),
    }


@router.post("/api/providers")
async def api_create_provider(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.providers_db import create_provider

    body = await request.body()
    params = parse_qs(body.decode(), keep_blank_values=True)
    if (
        not params.get("api_type", [""])[0].strip()
        and not params.get("catalog_id", [""])[0].strip()
    ):
        return HTMLResponse(content="Missing required field: api_type", status_code=400)
    try:
        provider_id = params["id"][0].strip()
        data = _provider_form_data(params, provider_id=provider_id)
        data["id"] = provider_id
        data["api_key"] = params.get("api_key", [""])[0] or None
        await create_provider(
            db_path,
            data,
        )
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except ValueError as exc:
        return HTMLResponse(content=str(exc), status_code=422)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_providers

    await _sync_provider_key_safe(
        db_path,
        {
            "id": provider_id,
            "prefix": data["prefix"],
            "base_url": data["base_url"],
            "api_key": data["api_key"],
        },
    )
    await reload_providers(request.app)
    return JSONResponse({"ok": True, "id": provider_id})


@router.put("/api/providers/{provider_id}")
async def api_update_provider(request: Request, provider_id: str) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.providers_db import update_provider

    body = await request.body()
    params = parse_qs(body.decode(), keep_blank_values=True)
    from janus.storage.providers_db import get_provider

    existing = await get_provider(db_path, provider_id)
    if existing is None:
        return HTMLResponse(content="Provider not found", status_code=404)
    if not params.get("api_type", [""])[0].strip() and not existing.get("api_type"):
        return HTMLResponse(content="Missing required field: api_type", status_code=400)
    new_key = params.get("api_key", [""])[0] or None
    if not new_key:
        new_key = existing["api_key"]
    try:
        data = _provider_form_data(params, provider_id=provider_id, existing=existing)
        data["api_key"] = new_key
        await update_provider(
            db_path,
            provider_id,
            data,
        )
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except ValueError as exc:
        return HTMLResponse(content=str(exc), status_code=422)
    except sqlite3.IntegrityError as exc:
        if "custom_models.provider_prefix, custom_models.model_id" in str(exc):
            return HTMLResponse(
                content="The destination prefix already has a custom model with the same ID",
                status_code=409,
            )
        return HTMLResponse(content="Provider update violates a data constraint", status_code=409)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_providers

    await _sync_provider_key_safe(
        db_path,
        {
            "id": provider_id,
            "prefix": data["prefix"],
            "base_url": data["base_url"],
            "api_key": new_key,
        },
    )
    await reload_providers(request.app)
    return JSONResponse({"ok": True, "id": provider_id})


@router.patch("/api/providers/{provider_id}/toggle")
async def api_toggle_provider(request: Request, provider_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.providers_db import toggle_provider

    await toggle_provider(db_path, provider_id)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return JSONResponse({"ok": True, "id": provider_id})


@router.delete("/api/providers/{provider_id}")
async def api_delete_provider(request: Request, provider_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.providers_db import delete_provider

    await delete_provider(db_path, provider_id)
    await _delete_mirrored_provider_key_safe(db_path, provider_id)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return JSONResponse({"ok": True, "id": provider_id})


async def _sync_provider_key_safe(db_path: Path, provider: dict[str, Any]) -> None:
    from janus.inventory.provider_key_sync import sync_provider_key

    try:
        await sync_provider_key(db_path, provider=provider, schedule_recheck=True)
    except Exception:
        logger.warning("Provider key mirror failed for %s", provider.get("id"), exc_info=True)


async def _delete_mirrored_provider_key_safe(db_path: Path, provider_id: str) -> None:
    from janus.inventory.provider_key_sync import delete_mirrored_provider_key

    try:
        await delete_mirrored_provider_key(db_path, provider_id)
    except Exception:
        logger.warning("Mirrored key cleanup failed for %s", provider_id, exc_info=True)


async def _resolve_provider_api_key(db_path: Path, provider: dict[str, Any]) -> str:
    api_key = provider.get("api_key") or ""
    if api_key:
        return str(api_key)
    from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
    from janus.storage.upstream_keys import get_probe_upstream_key

    inventory_id = inventory_provider_id_for_prefix(str(provider["prefix"]))
    probe = await get_probe_upstream_key(db_path, inventory_id)
    return probe or ""


async def _enrich_providers(db_path: Path) -> list[dict[str, Any]]:
    from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
    from janus.storage.providers_db import list_providers
    from janus.storage.upstream_keys import summarize_upstream_keys_for_inventory

    providers_raw = await list_providers(db_path)
    providers: list[dict[str, Any]] = []
    for p in providers_raw:
        parsed = dict(p)
        parsed["models_list"] = json.loads(parsed["models"]) if parsed["models"] else []
        parsed["allowed_models_list"] = (
            json.loads(parsed["allowed_models"]) if parsed.get("allowed_models") else []
        )
        inventory_id = inventory_provider_id_for_prefix(str(parsed["prefix"]))
        parsed["inventory_provider_id"] = inventory_id
        parsed["inventory_keys"] = await summarize_upstream_keys_for_inventory(
            db_path, inventory_id
        )
        parsed["quota"] = None
        if parsed.get("quota_window") and parsed.get("quota_limit"):
            from janus.storage.quotas import describe_reset, get_window_usage, quota_status

            try:
                usage = await get_window_usage(
                    db_path, str(parsed["id"]), str(parsed["quota_window"])
                )
                metric = parsed.get("quota_metric") or "requests"
                used = usage["tokens"] if metric == "tokens" else usage["requests"]
                limit = int(parsed["quota_limit"])
                status = quota_status(used, limit)
                parsed["quota"] = {
                    "used": used,
                    "limit": limit,
                    "metric": metric,
                    "window": parsed["quota_window"],
                    "percent": min(round(used * 100 / limit), 100) if limit else 0,
                    "exhausted": status == "exhausted",
                    "status": status,
                    **describe_reset(str(parsed["quota_window"])),
                }
            except Exception:
                parsed["quota"] = None
        providers.append(parsed)
    return providers


@router.post("/api/providers/fetch-models")
async def api_fetch_models(request: Request) -> JSONResponse:
    from urllib.parse import parse_qs

    import httpx

    from janus.inventory.url_guard import BROWSER_USER_AGENT

    db_path = await _ensure_db(request)
    body = await request.body()
    params = parse_qs(body.decode(), keep_blank_values=True)
    api_type = params.get("api_type", [""])[0]
    base_url = params.get("base_url", [""])[0].rstrip("/")
    api_key = params.get("api_key", [""])[0]
    provider_id = params.get("provider_id", [""])[0]
    catalog_id = params.get("catalog_id", [""])[0]
    if not api_key and provider_id:
        from janus.storage.providers_db import get_provider

        provider = await get_provider(db_path, provider_id)
        if provider:
            api_key = await _resolve_provider_api_key(db_path, provider)
            catalog_id = str(provider.get("catalog_id") or catalog_id)

    preset = get_catalog().get(catalog_id)
    unsafe = _reject_unsafe_url(
        base_url,
        allow_private_network=bool(preset and preset.get("allow_private_network")),
    )
    if unsafe is not None:
        return unsafe

    try:
        if api_type == "openai_compat":
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "User-Agent": BROWSER_USER_AGENT,
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Upstream returned {resp.status_code}"}, status_code=502
                )
            data = resp.json()
            models = sorted(m["id"] for m in data.get("data", []) if "id" in m)
            return JSONResponse({"models": models})

        if api_type == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "User-Agent": BROWSER_USER_AGENT,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base_url}/v1/models", headers=headers)
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Upstream returned {resp.status_code}"}, status_code=502
                )
            data = resp.json()
            models = sorted(m["id"] for m in data.get("data", []) if "id" in m)
            return JSONResponse({"models": models})

        if api_type == "gemini":
            params_dict: dict[str, str] = {}
            if api_key:
                params_dict["key"] = api_key
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{base_url}/v1beta/models",
                    params=params_dict,
                    headers={"User-Agent": BROWSER_USER_AGENT},
                )
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Upstream returned {resp.status_code}"}, status_code=502
                )
            data = resp.json()
            models = sorted(
                m["name"].replace("models/", "") for m in data.get("models", []) if "name" in m
            )
            return JSONResponse({"models": models})

        if api_type == "antigravity":
            from janus.inventory.antigravity_credentials import normalize_antigravity_credential
            from janus.inventory.key_checker import validate_key
            from janus.providers.oauth_tokens import access_token, parse_credential

            if not api_key:
                return JSONResponse(
                    {"error": "No Antigravity OAuth credential available"}, status_code=400
                )
            try:
                normalized = normalize_antigravity_credential(api_key)
                validation = await validate_key(normalized, "antigravity")
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if not validation.get("is_valid"):
                return JSONResponse(
                    {"error": validation.get("error", "Antigravity credential is invalid")},
                    status_code=502,
                )
            credential = parse_credential(str(validation.get("key_value") or normalized))
            token = access_token(credential)
            if not token:
                return JSONResponse(
                    {"error": "No Antigravity access token available"}, status_code=502
                )
            models_url = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "antigravity/ide/2.1.1 darwin/arm64",
                "X-Client-Name": "antigravity",
                "X-Client-Version": "2.1.1",
            }
            project = (credential.get("extra") or {}).get("projectId")
            payload = {"project": project} if isinstance(project, str) and project else {}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(models_url, headers=headers, json=payload)
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Upstream returned {resp.status_code}"}, status_code=502
                )
            data = resp.json()
            raw_models = data.get("models", {}) if isinstance(data, dict) else {}
            if isinstance(raw_models, dict):
                models = sorted(
                    str(model_id)
                    for model_id, info in raw_models.items()
                    if isinstance(info, dict) and not info.get("isInternal")
                )
            elif isinstance(raw_models, list):
                models = sorted(
                    str(item.get("id") or item.get("name"))
                    for item in raw_models
                    if isinstance(item, dict) and (item.get("id") or item.get("name"))
                )
            else:
                models = []
            return JSONResponse({"models": models})

        if api_type == "kiro":
            from janus.providers.kiro import KiroProvider

            if not api_key:
                return JSONResponse({"error": "No Kiro credential available"}, status_code=400)
            kiro_credentials: list[str] = [api_key]
            if provider_id:
                # A gateway backed by inventory has many credentials. The first
                # routable account may be valid for chat but lack permission for
                # ListAvailableModels, so try all active accounts until one can
                # return the shared Kiro catalog.
                from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
                from janus.storage.providers_db import get_provider
                from janus.storage.upstream_keys import list_routable_upstream_keys

                provider = await get_provider(db_path, provider_id)
                if provider:
                    inventory_id = inventory_provider_id_for_prefix(str(provider["prefix"]))
                    rows = await list_routable_upstream_keys(db_path, inventory_id)
                    inventory_credentials = [str(row["key_value"]) for row in rows]
                    if inventory_credentials:
                        kiro_credentials = inventory_credentials

            errors: list[str] = []
            for kiro_credential in kiro_credentials:
                kiro = KiroProvider(api_key=kiro_credential, base_url=base_url)
                try:
                    kiro_models, error = await kiro.list_models()
                finally:
                    await kiro.close()
                if kiro_models:
                    return JSONResponse({"models": kiro_models})
                if error and error not in errors:
                    errors.append(error)
            detail = "; ".join(errors[:3]) or "No Kiro account returned a model catalog"
            return JSONResponse({"error": detail}, status_code=502)

        if api_type == "github_copilot":
            from janus.providers.github_copilot import GitHubCopilotProvider

            copilot = GitHubCopilotProvider(oauth_token=api_key or "", base_url=base_url)
            try:
                copilot_models = await copilot.list_models()
            finally:
                await copilot.close()
            if not copilot_models:
                return JSONResponse(
                    {"error": "No models returned (is the GitHub token valid?)"},
                    status_code=502,
                )
            return JSONResponse({"models": sorted(copilot_models)})

        return JSONResponse(
            {"error": f"Fetch not supported for api_type: {api_type}"}, status_code=400
        )
    except httpx.TimeoutException:
        return JSONResponse({"error": "Request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(type(e).__name__)}, status_code=502)


@router.post("/api/oauth/copilot/start")
async def api_copilot_oauth_start(request: Request) -> JSONResponse:
    from janus.providers.github_copilot import start_device_flow

    try:
        data = await start_device_flow()
    except httpx.TimeoutException:
        return JSONResponse({"error": "GitHub request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(type(e).__name__)}, status_code=502)
    return JSONResponse(
        {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "interval": data["interval"],
            "expires_in": data["expires_in"],
        }
    )


@router.post("/api/oauth/copilot/poll")
async def api_copilot_oauth_poll(request: Request) -> JSONResponse:
    from urllib.parse import parse_qs

    from janus.providers.github_copilot import poll_device_flow

    body = await request.body()
    params = parse_qs(body.decode())
    device_code = params.get("device_code", [""])[0]
    if not device_code:
        return JSONResponse({"error": "Missing device_code"}, status_code=400)
    try:
        result = await poll_device_flow(device_code)
    except httpx.TimeoutException:
        return JSONResponse({"error": "GitHub request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(type(e).__name__)}, status_code=502)
    return JSONResponse(result)


@router.post("/api/providers/{provider_id}/test")
async def api_test_connection(request: Request, provider_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.providers_db import get_provider

    provider = await get_provider(db_path, provider_id)
    if not provider:
        return JSONResponse({"error": "Provider not found"}, status_code=404)

    models = json.loads(provider["models"]) if provider["models"] else []
    model = models[0] if models else ""
    api_type = provider["api_type"]
    base_url = provider["base_url"].rstrip("/")
    api_key = await _resolve_provider_api_key(db_path, provider)

    preset = get_catalog().get(str(provider.get("catalog_id") or ""))
    unsafe = _reject_unsafe_url(
        base_url,
        allow_private_network=bool(preset and preset.get("allow_private_network")),
    )
    if unsafe is not None:
        return unsafe

    try:
        start = time.perf_counter()
        if api_type == "openai_compat":
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            body: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)

        elif api_type == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{base_url}/v1/messages", headers=headers, json=body)

        elif api_type == "gemini":
            params: dict[str, str] = {}
            if api_key:
                params["key"] = api_key
            body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{base_url}/v1beta/models/{model}:generateContent",
                    params=params,
                    json=body,
                )
        elif api_type == "github_copilot":
            from janus.providers.github_copilot import GitHubCopilotProvider

            copilot = GitHubCopilotProvider(oauth_token=api_key or "", base_url=base_url)
            try:
                result = await copilot.call(
                    {
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                    stream=False,
                )
            finally:
                await copilot.close()
            latency_ms = round((time.perf_counter() - start) * 1000)
            ok = result.status_code < 400
            return JSONResponse(
                {"ok": ok, "status": result.status_code, "latency_ms": latency_ms}
                if ok
                else {
                    "ok": False,
                    "status": result.status_code,
                    "latency_ms": latency_ms,
                    "error": str(result.json_data)[:200] if result.json_data else "",
                }
            )
        else:
            return JSONResponse(
                {"error": f"Test not supported for api_type: {api_type}"}, status_code=400
            )

        latency_ms = round((time.perf_counter() - start) * 1000)
        ok = resp.status_code < 400
        return JSONResponse(
            {"ok": ok, "status": resp.status_code, "latency_ms": latency_ms}
            if ok
            else {"ok": False, "status": resp.status_code, "latency_ms": latency_ms}
        )
    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "error": "Request timed out"}, status_code=504)
    except (httpx.ConnectError, httpx.RequestError) as e:
        return JSONResponse({"ok": False, "error": str(type(e).__name__)}, status_code=502)


# ---- Combo CRUD ----


@router.post("/api/combos")
async def api_create_combo(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.combos_db import create_combo

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    try:
        await create_combo(db_path, {"name": params["name"][0], "models": models})
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_combos

    await reload_combos(request.app)
    return JSONResponse({"ok": True})


@router.put("/api/combos/{combo_id}")
async def api_update_combo(request: Request, combo_id: int) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.combos_db import update_combo

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    try:
        await update_combo(db_path, combo_id, {"name": params["name"][0], "models": models})
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_combos

    await reload_combos(request.app)
    return JSONResponse({"ok": True, "id": combo_id})


@router.delete("/api/combos/{combo_id}")
async def api_delete_combo(request: Request, combo_id: int) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.combos_db import delete_combo

    await delete_combo(db_path, combo_id)
    from janus.dashboard.reload import reload_combos

    await reload_combos(request.app)
    return JSONResponse({"ok": True, "id": combo_id})


# ---- Token Savers ----


def _saver_display_stats(raw_stats: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
    """Build per-saver display stats: saved KB, request count, avg % saved.

    Savings are clamped at >= 0 for display (prompt-injecting savers like
    Caveman/Ponytail can have negative raw savings); the underlying raw sums
    in the pipeline's stats dict are left untouched.
    """
    display: dict[str, dict[str, Any]] = {}
    for name, counters in raw_stats.items():
        requests = counters.get("requests", 0)
        if requests <= 0:
            continue
        bytes_before = counters.get("bytes_before", 0)
        bytes_after = counters.get("bytes_after", 0)
        saved_bytes = max(0, bytes_before - bytes_after)
        avg_pct = (saved_bytes / bytes_before * 100) if bytes_before else 0.0
        display[name] = {
            "requests": requests,
            "saved_kb": saved_bytes / 1024,
            "avg_pct": avg_pct,
        }
    return display


async def _savers_context(request: Request, db_path: Path) -> dict[str, Any]:
    from janus.storage.settings import (
        ensure_saver_defaults,
        get_all_settings,
        resolve_saver_settings,
    )

    await ensure_saver_defaults(db_path)
    settings = resolve_saver_settings(await get_all_settings(db_path))
    saver_pipeline = getattr(request.app.state, "saver_pipeline", None)
    raw_stats = getattr(saver_pipeline, "stats", {}) if saver_pipeline is not None else {}
    saver_stats = _saver_display_stats(raw_stats)
    return {"request": request, "settings": settings, "saver_stats": saver_stats}


VALID_ACCOUNT_STRATEGIES = frozenset({"fill_first", "round_robin", "sticky_rr"})

# Settings keys that require server-side validation before being persisted. Each
# validator raises ValueError on bad input; the POST handler rejects with 400 and
# leaves the stored value untouched (page re-renders with the prior value on reload).
_SETTINGS_VALIDATORS: dict[str, Callable[[str], None]] = {
    "combo_strategy": lambda v: _require_choice(v, VALID_COMBO_STRATEGIES),
    "combo_sticky_limit": lambda v: _require_int(v, min_value=1),
    "combo_fusion_min_panel": lambda v: _require_int(v, min_value=1),
    "combo_fusion_straggler_grace_s": lambda v: _require_float(v, min_value=0, max_value=3600),
    "combo_fusion_hard_timeout_s": lambda v: _require_float(
        v, min_value=0, max_value=3600, exclusive_min=True
    ),
    "server_account_strategy": lambda v: _require_choice(v, VALID_ACCOUNT_STRATEGIES),
    "server_sticky_limit": lambda v: _require_int(v, min_value=1),
    "server_gateway_rate_limit_rpm": lambda v: _require_int(v, min_value=0, max_value=100_000),
    "server_reporting_timezone": lambda v: _require_reporting_timezone(v),
}


def _require_choice(value: str, choices: frozenset[str]) -> None:
    if value not in choices:
        raise ValueError(f"must be one of: {', '.join(sorted(choices))}")


def _require_reporting_timezone(value: str) -> None:
    validate_reporting_timezone(value)


def _require_int(value: str, *, min_value: int, max_value: int | None = None) -> None:
    try:
        parsed = int(value)
    except ValueError as e:
        raise ValueError("must be an integer") from e
    if parsed < min_value:
        raise ValueError(f"must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"must be <= {max_value}")


def _require_float(
    value: str,
    *,
    min_value: float,
    max_value: float | None = None,
    exclusive_min: bool = False,
) -> None:
    try:
        parsed = float(value)
    except ValueError as e:
        raise ValueError("must be a number") from e
    if not math.isfinite(parsed):
        raise ValueError("must be a finite number")
    if exclusive_min:
        if parsed <= min_value:
            raise ValueError(f"must be > {min_value}")
    elif parsed < min_value:
        raise ValueError(f"must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"must be <= {max_value}")


@router.post("/api/settings")
async def api_update_setting(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.settings import set_setting

    body = await request.body()
    params = parse_qs(body.decode(), keep_blank_values=True)
    try:
        key = params["key"][0]
        value = params["value"][0]
    except KeyError:
        return HTMLResponse(content="Missing key or value", status_code=400)
    validator = _SETTINGS_VALIDATORS.get(key)
    if validator is not None:
        try:
            validator(value)
        except ValueError as e:
            return HTMLResponse(content=f"Invalid value for {key}: {e}", status_code=400)
    if key == "server_reporting_timezone":
        value = value.strip()
    await set_setting(db_path, key, value)
    if key.startswith("saver_"):
        from janus.dashboard.reload import reload_savers

        await reload_savers(request.app)
    if key == "server_cooldowns_enabled":
        from janus.storage.settings import cooldowns_enabled as cooldowns_flag

        handler = getattr(request.app.state, "fallback_handler", None)
        if handler is not None:
            handler.cooldowns_enabled = cooldowns_flag({key: value})
    return JSONResponse({"ok": True, "key": key})


# ---- Pricing ----


def _humanize_age(last_sync_raw: str | None) -> str | None:
    """Render a human-friendly "X ago" string for an ISO timestamp, or None."""
    if not last_sync_raw:
        return None
    try:
        last_sync = datetime.fromisoformat(last_sync_raw)
    except ValueError:
        return None
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=UTC)
    seconds = max(0.0, (datetime.now(UTC) - last_sync).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    return f"{int(days)}d ago"


def _pricing_sync_status(last_sync_raw: str | None) -> dict[str, Any]:
    stale = True
    if last_sync_raw:
        try:
            last_sync = datetime.fromisoformat(last_sync_raw)
            if last_sync.tzinfo is None:
                last_sync = last_sync.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - last_sync).total_seconds() / 3600
            stale = age_hours >= 48
        except ValueError:
            stale = True
    return {
        "last_sync_at": last_sync_raw,
        "last_sync_ago": _humanize_age(last_sync_raw),
        "stale": stale,
    }


async def _unpriced_models_context(request: Request, db_path: Path) -> list[dict[str, Any]]:
    """Models with recent zero-cost usage that the *current* registry still can't price."""
    registry = request.app.state.pricing_registry
    candidates = await get_unpriced_models(db_path)
    return [row for row in candidates if registry.get(row["model"]) is None]


async def _pricing_page_context(request: Request, db_path: Path) -> dict[str, Any]:
    from janus.pricing.builtin import BUILTIN_PRICING
    from janus.storage.pricing_catalog import list_catalog
    from janus.storage.pricing_db import list_pricing_overrides

    overrides = await list_pricing_overrides(db_path)
    builtin_list = [
        {
            "model": k,
            "input_per_mtok": p.input_per_mtok,
            "output_per_mtok": p.output_per_mtok,
            "cache_creation_per_mtok": p.cache_creation_per_mtok,
            "cache_read_per_mtok": p.cache_read_per_mtok,
        }
        for k, p in sorted(BUILTIN_PRICING.items())
    ]

    registry = request.app.state.pricing_registry
    catalog_rows = await list_catalog(db_path)
    catalog_list = [
        {
            "model": row["model"],
            "input_per_mtok": row["input_per_mtok"],
            "output_per_mtok": row["output_per_mtok"],
            "cache_creation_per_mtok": row["cache_creation_per_mtok"],
            "cache_read_per_mtok": row["cache_read_per_mtok"],
            # The catalog row's own source is always "catalog" -- source_of()
            # instead reports which layer actually *wins* for this model name,
            # so an override or a shorter builtin prefix match can shadow it.
            "source": registry.source_of(row["model"]),
        }
        for row in catalog_rows
    ]

    last_sync_raw = await get_setting(db_path, "pricing_last_sync_at")
    catalog_count_raw = await get_setting(db_path, "pricing_catalog_count")

    context: dict[str, Any] = {
        "request": request,
        "builtin": builtin_list,
        "overrides": overrides,
        "catalog": catalog_list,
        "sync_status": _pricing_sync_status(last_sync_raw),
        "catalog_count": int(catalog_count_raw) if catalog_count_raw else 0,
        "unpriced": await _unpriced_models_context(request, db_path),
    }
    return context


@router.post("/api/pricing")
async def api_create_pricing(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.pricing_db import create_or_update_pricing_override

    body = await request.body()
    params = parse_qs(body.decode())
    try:
        await create_or_update_pricing_override(
            db_path,
            {
                "model": params["model"][0],
                "input_per_mtok": float(params["input_per_mtok"][0]),
                "output_per_mtok": float(params["output_per_mtok"][0]),
                "cache_creation_per_mtok": float(params.get("cache_creation_per_mtok", ["0"])[0]),
                "cache_read_per_mtok": float(params.get("cache_read_per_mtok", ["0"])[0]),
            },
        )
    except (KeyError, ValueError) as e:
        return HTMLResponse(content=f"Invalid input: {e}", status_code=400)
    from janus.dashboard.reload import reload_pricing

    await reload_pricing(request.app)
    return JSONResponse({"ok": True})


@router.delete("/api/pricing/{model:path}")
async def api_delete_pricing(request: Request, model: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.pricing_db import delete_pricing_override

    await delete_pricing_override(db_path, model)
    from janus.dashboard.reload import reload_pricing

    await reload_pricing(request.app)
    return JSONResponse({"ok": True, "model": model})


@router.post("/api/pricing/sync")
async def api_sync_pricing(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.dashboard.reload import reload_pricing
    from janus.pricing.sync import PricingSyncError, fetch_and_sync

    try:
        count = await fetch_and_sync(db_path)
    except PricingSyncError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    await reload_pricing(request.app)
    synced_at = await get_setting(db_path, "pricing_last_sync_at")
    return JSONResponse({"count": count, "synced_at": synced_at})


@router.get("/api/export")
async def api_export_config(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from janus.storage.combos_db import list_combos
    from janus.storage.custom_models import list_custom_models
    from janus.storage.pricing_db import list_pricing_overrides
    from janus.storage.providers_db import list_providers

    providers_raw = await list_providers(db_path)
    providers_yaml = [
        {
            "id": p["id"],
            "catalog_id": p.get("catalog_id"),
            "prefix": p["prefix"],
            "api_type": p["api_type"],
            "base_url": p["base_url"],
            "api_key": p["api_key"],
            "models": json.loads(p["models"]) if p["models"] else [],
            "default_model": p.get("default_model"),
            "live_models": bool(p.get("live_models", 1)),
            "selected_models": json.loads(p["selected_models"]) if p.get("selected_models") else [],
            "transports": json.loads(p["transports"]) if p.get("transports") else None,
            "allowed_models": json.loads(p["allowed_models"]) if p.get("allowed_models") else [],
            "quota_window": p.get("quota_window"),
            "quota_limit": p.get("quota_limit"),
            "quota_metric": p.get("quota_metric") or "requests",
        }
        for p in providers_raw
    ]

    combos_raw = await list_combos(db_path)
    combos_yaml = [
        {"name": c["name"], "models": json.loads(c["models"]) if c["models"] else []}
        for c in combos_raw
    ]

    overrides_raw = await list_pricing_overrides(db_path)
    pricing_yaml = {
        o["model"]: {
            "input_per_mtok": o["input_per_mtok"],
            "output_per_mtok": o["output_per_mtok"],
            "cache_creation_per_mtok": o["cache_creation_per_mtok"],
            "cache_read_per_mtok": o["cache_read_per_mtok"],
        }
        for o in overrides_raw
    }

    config_data: dict[str, Any] = {
        "server": {"port": request.app.state.config.server.port},
        "providers": providers_yaml,
    }
    custom_models = await list_custom_models(db_path)
    if custom_models:
        config_data["custom_models"] = [
            {
                "id": model["id"],
                "provider_id": model["provider_id"],
                "model_id": model["model_id"],
                "display_name": model["display_name"],
                "context_window": model["context_window"],
                "max_output_tokens": model["max_output_tokens"],
                "input_modalities": model["input_modalities"],
                "reasoning_efforts": model["reasoning_efforts"],
                "capabilities": model["capabilities"],
                "is_enabled": model["is_enabled"],
            }
            for model in custom_models
        ]
    if combos_yaml:
        config_data["combos"] = combos_yaml
    if pricing_yaml:
        config_data["pricing"] = pricing_yaml

    yaml_text = yaml.safe_dump(config_data, sort_keys=False)
    return Response(
        content=yaml_text,
        media_type="text/yaml",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="janus-config.yaml"',
            "Expires": "0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/reset")
async def api_reset_to_defaults(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    from janus.storage.database import get_connection, seed_from_config

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM custom_models")
        await db.execute("DELETE FROM providers")
        await db.execute("DELETE FROM combos")
        await db.execute("DELETE FROM pricing_overrides")
        await db.execute("DELETE FROM settings")
        await db.commit()

    from janus.storage.settings import invalidate_settings_cache

    invalidate_settings_cache(db_path)

    await seed_from_config(db_path, request.app.state.config)

    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )

    await reload_providers(request.app)
    await reload_combos(request.app)
    await reload_savers(request.app)
    await reload_pricing(request.app)
    return JSONResponse({"ok": True})
