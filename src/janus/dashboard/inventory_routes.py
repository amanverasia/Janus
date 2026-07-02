from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from janus.dashboard.auth import require_dashboard_access
from janus.dashboard.routes import _ensure_db, _templates
from janus.inventory.catalog import get_inventory_providers
from janus.inventory.key_checker import check_all_upstream_keys, check_upstream_key
from janus.inventory.url_guard import detect_provider_from_key, is_http_url
from janus.storage.inventory_overview import (
    get_credit_summary,
    get_inventory_summary,
    get_provider_cards,
    get_recent_activity,
)
from janus.storage.inventory_providers import list_inventory_providers
from janus.storage.upstream_keys import (
    create_upstream_key,
    delete_upstream_key,
    export_upstream_keys,
    list_upstream_keys_masked,
)

router = APIRouter(dependencies=[Depends(require_dashboard_access)])


def _schedule_recheck(key_id: str, db_path: Path) -> None:
    asyncio.create_task(check_upstream_key(db_path, key_id))


def _parse_bulk_keys(raw: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append({"label": "", "key": line})
    return entries


def _status_badge(status: str) -> str:
    mapping = {
        "active": "bg-green-900 text-green-200",
        "invalid": "bg-red-900 text-red-200",
        "pending_validation": "bg-yellow-900 text-yellow-200",
        "error": "bg-orange-900 text-orange-200",
        "daily_exhausted": "bg-purple-900 text-purple-200",
    }
    return mapping.get(status, "bg-gray-700 text-gray-200")


@router.get("/inventory", response_class=HTMLResponse)
async def inventory_overview_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    provider_cards = [
        card for card in await get_provider_cards(db_path) if int(card.get("total_keys") or 0) > 0
    ]
    context: dict[str, Any] = {
        "request": request,
        "summary": await get_inventory_summary(db_path),
        "provider_cards": provider_cards,
        "recent_activity": await get_recent_activity(db_path),
        "credit_summary": await get_credit_summary(db_path),
        "status_badge": _status_badge,
    }
    return _templates.TemplateResponse(request, "inventory_overview.html", context)


@router.get("/inventory/keys", response_class=HTMLResponse)
async def inventory_keys_page(
    request: Request,
    provider_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    providers = await list_inventory_providers(db_path, active_only=True)
    keys = await list_upstream_keys_masked(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
    )
    provider_names = {p["id"]: p["display_name"] for p in providers}
    for key in keys:
        key["provider_display_name"] = provider_names.get(key["provider_id"], key["provider_id"])
    context: dict[str, Any] = {
        "request": request,
        "keys": keys,
        "providers": providers,
        "provider_id": provider_id or "",
        "status": status or "",
        "search": search or "",
        "status_badge": _status_badge,
        "submit_message": None,
    }
    return _templates.TemplateResponse(request, "inventory_keys.html", context)


@router.get("/inventory/add", response_class=HTMLResponse)
async def inventory_add_page(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    providers = await list_inventory_providers(db_path, active_only=True)
    context: dict[str, Any] = {
        "request": request,
        "providers": providers,
        "results": None,
        "error": None,
    }
    return _templates.TemplateResponse(request, "inventory_add.html", context)


async def _keys_partial(
    request: Request,
    db_path: Path,
    *,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    submit_message: str | None = None,
) -> HTMLResponse:
    providers = await list_inventory_providers(db_path, active_only=True)
    keys = await list_upstream_keys_masked(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
    )
    provider_names = {p["id"]: p["display_name"] for p in providers}
    for key in keys:
        key["provider_display_name"] = provider_names.get(key["provider_id"], key["provider_id"])
    context: dict[str, Any] = {
        "request": request,
        "keys": keys,
        "providers": providers,
        "provider_id": provider_id,
        "status": status,
        "search": search,
        "status_badge": _status_badge,
        "submit_message": submit_message,
    }
    return _templates.TemplateResponse(request, "inventory_keys_partial.html", context)


@router.post("/api/inventory/submit", response_class=HTMLResponse)
async def api_inventory_submit(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    entries = _parse_bulk_keys(keys_text)
    base_url = custom_base_url.strip() or None
    if base_url and not is_http_url(base_url):
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {"request": request, "results": [], "error": "Invalid custom base URL."},
        )

    results: list[dict[str, Any]] = []
    for entry in entries:
        key_value = entry["key"]
        resolved_provider = provider_id
        if resolved_provider == "auto":
            resolved_provider = detect_provider_from_key(key_value) or "unidentified"
        if resolved_provider == "custom" and not base_url:
            results.append(
                {
                    "key_masked": key_value[:8] + "…",
                    "provider_id": resolved_provider,
                    "status": "skipped",
                    "error": "Custom provider requires a base URL.",
                }
            )
            continue
        record = await create_upstream_key(
            db_path,
            provider_id=resolved_provider,
            key_value=key_value,
            custom_base_url=base_url if resolved_provider == "custom" else None,
        )
        _schedule_recheck(record["id"], db_path)
        results.append(
            {
                "id": record["id"],
                "key_masked": record["key_masked"],
                "provider_id": resolved_provider,
                "status": "pending_validation",
            }
        )

    return _templates.TemplateResponse(
        request,
        "inventory_add_results.html",
        {"request": request, "results": results, "error": None},
    )


@router.post("/api/inventory/keys", response_class=HTMLResponse)
async def api_submit_upstream_keys(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    entries = _parse_bulk_keys(keys_text)
    if not entries:
        return await _keys_partial(request, db_path, submit_message="No keys found in input.")

    base_url = custom_base_url.strip() or None
    if base_url and not is_http_url(base_url):
        return await _keys_partial(request, db_path, submit_message="Invalid custom base URL.")

    created = 0
    for entry in entries:
        key_value = entry["key"]
        resolved_provider = provider_id
        if resolved_provider == "auto":
            resolved_provider = detect_provider_from_key(key_value) or "unidentified"
        if resolved_provider == "custom" and not base_url:
            continue
        record = await create_upstream_key(
            db_path,
            provider_id=resolved_provider,
            key_value=key_value,
            key_label=entry["label"] or None,
            custom_base_url=base_url if resolved_provider == "custom" else None,
        )
        _schedule_recheck(record["id"], db_path)
        created += 1

    message = f"Added {created} key(s). Validation running in background."
    return await _keys_partial(request, db_path, submit_message=message)


@router.post("/api/inventory/keys/{key_id}/recheck", response_class=HTMLResponse)
async def api_recheck_upstream_key(request: Request, key_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await check_upstream_key(db_path, key_id)
    return await _keys_partial(request, db_path, submit_message="Recheck complete.")


@router.delete("/api/inventory/keys/{key_id}", response_class=HTMLResponse)
async def api_delete_upstream_key(request: Request, key_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await delete_upstream_key(db_path, key_id)
    return await _keys_partial(request, db_path)


@router.post("/api/inventory/recheck-all", response_class=HTMLResponse)
async def api_recheck_all_upstream_keys(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    count = await check_all_upstream_keys(db_path)
    return await _keys_partial(
        request,
        db_path,
        submit_message=f"Rechecked {count} key(s).",
    )


@router.get("/api/inventory/export")
async def api_export_upstream_keys(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    exported = await export_upstream_keys(db_path)
    return JSONResponse(
        {
            "exported_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "count": len(exported),
            "keys": exported,
        }
    )


@router.get("/api/inventory/providers")
async def api_list_inventory_providers(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    providers = await list_inventory_providers(db_path, active_only=True)
    return JSONResponse({"providers": providers, "catalog": get_inventory_providers()})
