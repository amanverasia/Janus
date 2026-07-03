from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from janus.api.auth import authenticate_api_key
from janus.dashboard.auth import require_dashboard_access
from janus.dashboard.catalog import get_provider_logo_map, provider_logo_url
from janus.dashboard.credentials import (
    SESSION_COOKIE,
    SETTINGS_PASSWORD_HASH,
    SETTINGS_USERNAME,
    create_session_token,
    get_or_create_session_secret,
    hash_password,
    is_password_login_configured,
    verify_password,
)
from janus.storage.analytics import (
    Dimension,
    get_breakdown,
    get_flow,
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
from janus.storage.settings import get_setting, set_setting
from janus.storage.usage import get_usage_stats

router = APIRouter(dependencies=[Depends(require_dashboard_access)])

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_templates.env.filters["urlencode"] = lambda value: quote(str(value))
_templates.env.globals["provider_logo_url"] = provider_logo_url

try:
    from importlib.metadata import version as _pkg_version

    _templates.env.globals["janus_version"] = _pkg_version("janus-ai")
except Exception:
    _templates.env.globals["janus_version"] = "0.0.0"


def _api_v1_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/v1"


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
async def login_page(request: Request, next: str = "/dashboard") -> HTMLResponse:
    if not next.startswith("/dashboard"):
        next = "/dashboard"
    db_path = await _ensure_db(request)
    context: dict[str, Any] = {
        "request": request,
        "next": next,
        "error": None,
        "password_login_enabled": await is_password_login_configured(db_path),
    }
    return _templates.TemplateResponse(request, "login.html", context)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    api_key: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/dashboard"),
) -> Response:
    if not next.startswith("/dashboard"):
        next = "/dashboard"
    db_path = await _ensure_db(request)
    password_login_enabled = await is_password_login_configured(db_path)

    if username.strip() or password:
        if not password_login_enabled:
            context: dict[str, Any] = {
                "request": request,
                "next": next,
                "error": "Username/password login is not configured",
                "password_login_enabled": False,
            }
            return _templates.TemplateResponse(request, "login.html", context, status_code=401)
        stored_username = await get_setting(db_path, SETTINGS_USERNAME)
        stored_hash = await get_setting(db_path, SETTINGS_PASSWORD_HASH)
        if (
            not stored_username
            or not stored_hash
            or username.strip() != stored_username
            or not verify_password(password, stored_hash)
        ):
            context = {
                "request": request,
                "next": next,
                "error": "Invalid username or password",
                "password_login_enabled": password_login_enabled,
            }
            return _templates.TemplateResponse(request, "login.html", context, status_code=401)
        secret = await get_or_create_session_secret(db_path)
        token = create_session_token(secret, stored_username)
        response = RedirectResponse(url=next, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
        )
        return response

    if not api_key.strip():
        context = {
            "request": request,
            "next": next,
            "error": "API key is required",
            "password_login_enabled": password_login_enabled,
        }
        return _templates.TemplateResponse(request, "login.html", context, status_code=401)

    if not await authenticate_api_key(request, api_key.strip()):
        context = {
            "request": request,
            "next": next,
            "error": "Invalid API key",
            "password_login_enabled": password_login_enabled,
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
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    stats = await _get_usage_stats_safe(db_path)
    from janus.storage.providers_db import list_providers

    provider_count = len(await list_providers(db_path, enabled_only=True))
    registry = request.app.state.registry
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
        "base_url": _api_v1_base_url(request),
    }
    return _templates.TemplateResponse(request, "overview.html", context)


@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.dashboard.catalog import get_catalog

    context: dict[str, Any] = {
        "request": request,
        "providers": await _enrich_providers(db_path),
        "catalog": get_catalog(),
        "logo_map": get_provider_logo_map(),
    }
    return _templates.TemplateResponse(request, "providers.html", context)


@router.get("/combos", response_class=HTMLResponse)
async def combos_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.combos_db import list_combos

    combos_raw = await list_combos(db_path)
    combos = []
    for c in combos_raw:
        parsed = dict(c)
        parsed["models_list"] = json.loads(parsed["models"]) if parsed["models"] else []
        combos.append(parsed)
    context: dict[str, Any] = {
        "request": request,
        "combos": combos,
    }
    return _templates.TemplateResponse(request, "combos.html", context)


@router.get("/routing", response_class=HTMLResponse)
async def routing_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.routing_overview import get_routing_overview

    overview = await get_routing_overview(db_path)
    context: dict[str, Any] = {
        "request": request,
        "overview": overview,
    }
    return _templates.TemplateResponse(request, "routing.html", context)


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
    days = max(1, min(days, 365))
    try:
        summary = await get_spend_summary(db_path, days=days)
        breakdown = await get_breakdown(db_path, dimension=cast(Dimension, dimension), days=days)
        success = await get_success_rate(db_path, days=days)
        flow = await get_flow(db_path, days=days)
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
        flow = {"nodes": [], "links": []}
    context: dict[str, Any] = {
        "request": request,
        "summary": summary,
        "breakdown": breakdown,
        "success": success,
        "flow": flow,
        "days": days,
        "dimension": dimension,
    }
    return _templates.TemplateResponse(request, "analytics.html", context)


@router.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    try:
        budget_statuses, keys = await _build_budget_statuses(db_path)
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


async def _build_budget_statuses(
    db_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    return budget_statuses, keys


async def _budgets_partial(request: Request, db_path: Path) -> HTMLResponse:
    try:
        budget_statuses, keys = await _build_budget_statuses(db_path)
    except Exception:
        budget_statuses = []
        keys = []
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


# ---- Provider CRUD ----


@router.post("/api/providers", response_class=HTMLResponse)
async def api_create_provider(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.providers_db import create_provider

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    try:
        await create_provider(
            db_path,
            {
                "id": params["id"][0],
                "prefix": params["prefix"][0],
                "api_type": params["api_type"][0],
                "base_url": params.get("base_url", [""])[0],
                "api_key": params.get("api_key", [""])[0] or None,
                "models": models,
            },
        )
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.put("/api/providers/{provider_id}", response_class=HTMLResponse)
async def api_update_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.providers_db import update_provider

    body = await request.body()
    params = parse_qs(body.decode())
    models_str = params.get("models", [""])[0]
    models = [m.strip() for m in models_str.split(",") if m.strip()]
    new_key = params.get("api_key", [""])[0] or None
    if not new_key:
        from janus.storage.providers_db import get_provider

        existing = await get_provider(db_path, provider_id)
        new_key = existing["api_key"] if existing else None
    try:
        await update_provider(
            db_path,
            provider_id,
            {
                "prefix": params["prefix"][0],
                "api_type": params["api_type"][0],
                "base_url": params.get("base_url", [""])[0],
                "api_key": new_key,
                "models": models,
            },
        )
    except KeyError:
        return HTMLResponse(content="Missing required field", status_code=400)
    except Exception as e:
        return HTMLResponse(content=str(type(e).__name__), status_code=400)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.patch("/api/providers/{provider_id}/toggle", response_class=HTMLResponse)
async def api_toggle_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.providers_db import toggle_provider

    await toggle_provider(db_path, provider_id)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


@router.delete("/api/providers/{provider_id}", response_class=HTMLResponse)
async def api_delete_provider(request: Request, provider_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.providers_db import delete_provider

    await delete_provider(db_path, provider_id)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return await _providers_partial(request, db_path)


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
        inventory_id = inventory_provider_id_for_prefix(str(parsed["prefix"]))
        parsed["inventory_provider_id"] = inventory_id
        parsed["inventory_keys"] = await summarize_upstream_keys_for_inventory(
            db_path, inventory_id
        )
        providers.append(parsed)
    return providers


async def _providers_partial(request: Request, db_path: Path) -> HTMLResponse:
    context: dict[str, Any] = {
        "request": request,
        "providers": await _enrich_providers(db_path),
        "logo_map": get_provider_logo_map(),
    }
    return _templates.TemplateResponse(request, "providers_partial.html", context)


@router.post("/api/providers/fetch-models")
async def api_fetch_models(request: Request) -> JSONResponse:
    from urllib.parse import parse_qs

    import httpx

    db_path = await _ensure_db(request)
    body = await request.body()
    params = parse_qs(body.decode())
    api_type = params.get("api_type", [""])[0]
    base_url = params.get("base_url", [""])[0].rstrip("/")
    api_key = params.get("api_key", [""])[0]
    provider_id = params.get("provider_id", [""])[0]
    if not api_key and provider_id:
        from janus.storage.providers_db import get_provider

        provider = await get_provider(db_path, provider_id)
        if provider:
            api_key = await _resolve_provider_api_key(db_path, provider)

    allowed_schemes = ("http", "https")
    try:
        parsed = httpx.URL(base_url)
    except Exception:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    if parsed.scheme not in allowed_schemes:
        return JSONResponse({"error": "Only http/https URLs are allowed"}, status_code=400)
    import ipaddress
    import socket

    try:
        hostname = parsed.host
        if hostname:
            addr_info = socket.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in addr_info:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return JSONResponse(
                        {"error": "URLs pointing to internal/private addresses are not allowed"},
                        status_code=400,
                    )
    except (socket.gaierror, ValueError):
        pass

    try:
        if api_type == "openai_compat":
            headers: dict[str, str] = {"Content-Type": "application/json"}
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
                resp = await client.get(f"{base_url}/v1beta/models", params=params_dict)
            if resp.status_code != 200:
                return JSONResponse(
                    {"error": f"Upstream returned {resp.status_code}"}, status_code=502
                )
            data = resp.json()
            models = sorted(
                m["name"].replace("models/", "") for m in data.get("models", []) if "name" in m
            )
            return JSONResponse({"models": models})

        return JSONResponse(
            {"error": f"Fetch not supported for api_type: {api_type}"}, status_code=400
        )
    except httpx.TimeoutException:
        return JSONResponse({"error": "Request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(type(e).__name__)}, status_code=502)


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


@router.post("/api/combos", response_class=HTMLResponse)
async def api_create_combo(request: Request) -> HTMLResponse:
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
    return await _combos_partial(request, db_path)


@router.put("/api/combos/{combo_id}", response_class=HTMLResponse)
async def api_update_combo(request: Request, combo_id: int) -> HTMLResponse:
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
    return await _combos_partial(request, db_path)


@router.delete("/api/combos/{combo_id}", response_class=HTMLResponse)
async def api_delete_combo(request: Request, combo_id: int) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.combos_db import delete_combo

    await delete_combo(db_path, combo_id)
    from janus.dashboard.reload import reload_combos

    await reload_combos(request.app)
    return await _combos_partial(request, db_path)


async def _combos_partial(request: Request, db_path: Path) -> HTMLResponse:
    from janus.storage.combos_db import list_combos

    combos_raw = await list_combos(db_path)
    combos = []
    for c in combos_raw:
        parsed = dict(c)
        parsed["models_list"] = json.loads(parsed["models"]) if parsed["models"] else []
        combos.append(parsed)
    context: dict[str, Any] = {
        "request": request,
        "combos": combos,
    }
    return _templates.TemplateResponse(request, "combos_partial.html", context)


# ---- Token Savers ----


async def _savers_context(request: Request, db_path: Path) -> dict[str, Any]:
    from janus.storage.settings import ensure_saver_defaults, get_all_settings, resolve_saver_settings

    await ensure_saver_defaults(db_path)
    settings = resolve_saver_settings(await get_all_settings(db_path))
    return {"request": request, "settings": settings}


@router.get("/savers", response_class=HTMLResponse)
async def savers_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    return _templates.TemplateResponse(
        request, "savers.html", await _savers_context(request, db_path)
    )


@router.get("/api/savers/partial", response_class=HTMLResponse)
async def savers_partial(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    return _templates.TemplateResponse(
        request, "savers_list.html", await _savers_context(request, db_path)
    )


@router.post("/api/settings", response_class=HTMLResponse)
async def api_update_setting(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from urllib.parse import parse_qs

    from janus.storage.settings import set_setting

    body = await request.body()
    params = parse_qs(body.decode())
    try:
        key = params["key"][0]
        value = params["value"][0]
    except KeyError:
        return HTMLResponse(content="Missing key or value", status_code=400)
    await set_setting(db_path, key, value)
    if key.startswith("saver_"):
        from janus.dashboard.reload import reload_savers

        await reload_savers(request.app)
    return HTMLResponse(content="", status_code=200)


# ---- Tool Setup ----


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request) -> HTMLResponse:
    await _ensure_db(request)
    from janus.api.auth import is_require_api_key_enabled

    require_key = await is_require_api_key_enabled(request)
    context: dict[str, Any] = {
        "request": request,
        "base_url": _api_v1_base_url(request),
        "require_key": require_key,
    }
    return _templates.TemplateResponse(request, "tools.html", context)


# ---- Pricing ----


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.pricing.builtin import BUILTIN_PRICING
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
    context: dict[str, Any] = {
        "request": request,
        "builtin": builtin_list,
        "overrides": overrides,
    }
    return _templates.TemplateResponse(request, "pricing.html", context)


@router.post("/api/pricing", response_class=HTMLResponse)
async def api_create_pricing(request: Request) -> HTMLResponse:
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
    return await _pricing_partial(request, db_path)


@router.delete("/api/pricing/{model}", response_class=HTMLResponse)
async def api_delete_pricing(request: Request, model: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.pricing_db import delete_pricing_override

    await delete_pricing_override(db_path, model)
    from janus.dashboard.reload import reload_pricing

    await reload_pricing(request.app)
    return await _pricing_partial(request, db_path)


async def _pricing_partial(request: Request, db_path: Path) -> HTMLResponse:
    from janus.pricing.builtin import BUILTIN_PRICING
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
    context: dict[str, Any] = {
        "request": request,
        "builtin": builtin_list,
        "overrides": overrides,
    }
    return _templates.TemplateResponse(request, "pricing_partial.html", context)


# ---- Settings ----


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.settings import (
        ensure_server_defaults,
        get_all_settings,
        require_api_key_enabled,
    )

    await ensure_server_defaults(db_path)
    settings = await get_all_settings(db_path)
    hidden_keys = {
        SETTINGS_PASSWORD_HASH,
        "dashboard_session_secret",
    }
    display_settings = {key: value for key, value in settings.items() if key not in hidden_keys}
    context: dict[str, Any] = {
        "request": request,
        "settings": display_settings,
        "config": request.app.state.config,
        "dashboard_username": settings.get(SETTINGS_USERNAME, ""),
        "dashboard_password_set": bool(settings.get(SETTINGS_PASSWORD_HASH)),
        "require_api_key_enabled": require_api_key_enabled(settings),
    }
    return _templates.TemplateResponse(request, "settings.html", context)


@router.post("/api/settings/credentials", response_class=HTMLResponse)
async def api_update_dashboard_credentials(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    username = username.strip()
    if not username:
        return HTMLResponse(content="Username is required", status_code=400)
    if len(password) < 8:
        return HTMLResponse(content="Password must be at least 8 characters", status_code=400)
    if password != password_confirm:
        return HTMLResponse(content="Passwords do not match", status_code=400)
    await set_setting(db_path, SETTINGS_USERNAME, username)
    await set_setting(db_path, SETTINGS_PASSWORD_HASH, hash_password(password))
    return HTMLResponse(
        '<span class="text-green-400">Dashboard credentials saved</span>',
        status_code=200,
    )


@router.get("/api/export")
async def api_export_config(request: Request) -> Response:
    db_path = await _ensure_db(request)
    from janus.storage.combos_db import list_combos
    from janus.storage.pricing_db import list_pricing_overrides
    from janus.storage.providers_db import list_providers

    providers_raw = await list_providers(db_path)
    providers_yaml = [
        {
            "id": p["id"],
            "prefix": p["prefix"],
            "api_type": p["api_type"],
            "base_url": p["base_url"],
            "api_key": p["api_key"],
            "models": json.loads(p["models"]) if p["models"] else [],
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
    if combos_yaml:
        config_data["combos"] = combos_yaml
    if pricing_yaml:
        config_data["pricing"] = pricing_yaml

    yaml_text = yaml.safe_dump(config_data, sort_keys=False)
    return Response(
        content=yaml_text,
        media_type="text/yaml",
        headers={"Content-Disposition": 'attachment; filename="janus-config.yaml"'},
    )


@router.post("/api/reset", response_class=HTMLResponse)
async def api_reset_to_defaults(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    from janus.storage.database import get_connection, seed_from_config

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM providers")
        await db.execute("DELETE FROM combos")
        await db.execute("DELETE FROM pricing_overrides")
        await db.execute("DELETE FROM settings")
        await db.commit()

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
    return HTMLResponse(content="", status_code=200)
