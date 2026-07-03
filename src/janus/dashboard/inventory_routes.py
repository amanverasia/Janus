from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

from janus.dashboard.auth import require_dashboard_access
from janus.dashboard.routes import _ensure_db, _templates
from janus.inventory.catalog import get_inventory_providers
from janus.inventory.ingestion import KeyIngestEntry, enforce_batch_size, ingest_upstream_key
from janus.inventory.key_checker import check_all_upstream_keys
from janus.inventory.key_encryption import encryption_enabled
from janus.inventory.migrate import import_dashboard_json, verify_inventory
from janus.inventory.rate_limit import get_submit_rate_limiter
from janus.inventory.recheck_scheduler import schedule_upstream_recheck
from janus.inventory.reclassify import reclassify_upstream_keys
from janus.routing.provider_provision import (
    build_provision_preview,
    ensure_routing_providers,
)
from janus.storage.inventory_overview import (
    get_best_upstream_keys,
    get_credit_summary,
    get_inventory_summary,
    get_provider_cards,
    get_recent_activity,
    get_top_keys_per_provider,
)
from janus.storage.inventory_providers import list_inventory_providers
from janus.storage.providers_db import list_providers
from janus.storage.upstream_keys import (
    DEFAULT_PAGE_SIZE,
    SORT_COLUMNS,
    count_pending_upstream_keys,
    count_storage_encryption_state,
    count_upstream_keys_filtered,
    delete_upstream_key,
    export_upstream_keys,
    get_upstream_key_detail,
    get_upstream_keys_by_ids,
    list_upstream_key_history,
    list_upstream_keys,
    list_upstream_keys_page,
    reencrypt_plaintext_upstream_keys,
    update_upstream_key,
)
from janus.storage.upstream_models import list_models_for_key

router = APIRouter(dependencies=[Depends(require_dashboard_access)])


def _client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _schedule_recheck(key_id: str, db_path: Path) -> None:
    schedule_upstream_recheck(key_id, db_path)


def _schedule_recheck_all(db_path: Path) -> None:
    asyncio.create_task(_run_all_keys(db_path))


async def _run_all_keys(db_path: Path) -> None:
    keys = await list_upstream_keys(db_path)
    for key in keys:
        await update_upstream_key(
            db_path,
            key["id"],
            {"status": "pending_validation", "last_error": None},
        )
    await check_all_upstream_keys(db_path)


def _poll_query(
    *,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    direction: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    key_ids: list[str] | None = None,
) -> str:
    params: dict[str, str] = {
        "sort": sort,
        "dir": direction,
        "limit": str(limit),
        "offset": str(offset),
    }
    if provider_id:
        params["provider_id"] = provider_id
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    if key_ids:
        params["ids"] = ",".join(key_ids)
    return urlencode(params)


def _clamp_page_size(limit: int) -> int:
    return max(1, min(limit, 200))


def _safe_json_field(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _sort_toggle(current_sort: str, current_dir: str, column: str) -> str:
    if current_sort == column:
        return "asc" if current_dir == "desc" else "desc"
    if column in {"provider", "status"}:
        return "asc"
    return "desc"


async def _keys_context(
    db_path: Path,
    *,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    direction: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    submit_message: str | None = None,
) -> dict[str, Any]:
    providers = await list_inventory_providers(db_path, active_only=True)
    page_size = _clamp_page_size(limit)
    total = await count_upstream_keys_filtered(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
    )
    if total > 0 and offset >= total:
        offset = max(0, ((total - 1) // page_size) * page_size)
    keys = await list_upstream_keys_page(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
        sort=sort,
        direction=direction,
        limit=page_size,
        offset=offset,
        masked=True,
    )
    has_pending = await count_pending_upstream_keys(db_path) > 0
    page = (offset // page_size) + 1 if total else 1
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "keys": keys,
        "providers": providers,
        "provider_id": provider_id,
        "status": status,
        "search": search,
        "sort": sort,
        "direction": direction,
        "limit": page_size,
        "offset": offset,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "sort_columns": SORT_COLUMNS,
        "status_badge": _status_badge,
        "submit_message": submit_message,
        "has_pending": has_pending,
        "sort_toggle": _sort_toggle,
        "poll_query": _poll_query(
            provider_id=provider_id,
            status=status,
            search=search,
            sort=sort,
            direction=direction,
            limit=page_size,
            offset=offset,
        ),
    }


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


async def _encryption_context(db_path: Path) -> dict[str, Any]:
    state = await count_storage_encryption_state(db_path)
    return {
        "encryption": state,
        "encryption_enabled": encryption_enabled(),
    }


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
        "best_keys": await get_best_upstream_keys(db_path),
        "top_keys": await get_top_keys_per_provider(db_path),
        **await _encryption_context(db_path),
    }
    return _templates.TemplateResponse(request, "inventory_overview.html", context)


@router.get("/inventory/keys", response_class=HTMLResponse)
async def inventory_keys_page(
    request: Request,
    provider_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    context = await _keys_context(
        db_path,
        provider_id=provider_id or "",
        status=status or "",
        search=search or "",
        sort=sort,
        direction=dir,
        limit=limit,
        offset=offset,
    )
    context["request"] = request
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


@router.get("/inventory/import", response_class=HTMLResponse)
async def inventory_import_page(request: Request) -> HTMLResponse:
    context: dict[str, Any] = {
        "request": request,
        "error": None,
        "imported_count": None,
        "verification": None,
        "filename": None,
    }
    return _templates.TemplateResponse(request, "inventory_import.html", context)


async def _keys_partial(
    request: Request,
    db_path: Path,
    *,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    direction: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    submit_message: str | None = None,
) -> HTMLResponse:
    context = await _keys_context(
        db_path,
        provider_id=provider_id,
        status=status,
        search=search,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
        submit_message=submit_message,
    )
    context["request"] = request
    return _templates.TemplateResponse(request, "inventory_keys_partial.html", context)


@router.get("/api/inventory/keys/partial", response_class=HTMLResponse)
async def api_inventory_keys_partial(
    request: Request,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    return await _keys_partial(
        request,
        db_path,
        provider_id=provider_id,
        status=status,
        search=search,
        sort=sort,
        direction=dir,
        limit=limit,
        offset=offset,
    )


@router.post("/api/inventory/preview", response_class=HTMLResponse)
async def api_inventory_preview(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    entries = [KeyIngestEntry(key=entry["key"]) for entry in _parse_bulk_keys(keys_text)]
    batch_error = enforce_batch_size(len(entries))
    if batch_error:
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {"request": request, "results": [], "error": batch_error},
        )
    if not entries:
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {"request": request, "results": [], "error": "No keys found in input."},
        )

    existing = await list_providers(db_path)
    preview = build_provision_preview(
        entries,
        chosen_provider=provider_id,
        existing_providers=existing,
    )
    total_keys = sum(g.key_count for g in preview.groups)

    return _templates.TemplateResponse(
        request,
        "inventory_add_preview.html",
        {
            "request": request,
            "preview": preview,
            "groups": preview.groups,
            "total_keys": total_keys,
            "keys_text": keys_text,
            "provider_id": provider_id,
            "custom_base_url": custom_base_url,
        },
    )


@router.post("/api/inventory/submit", response_class=HTMLResponse)
async def api_inventory_submit(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
    provision_routing: str = Form("false"),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    entries = [KeyIngestEntry(key=entry["key"]) for entry in _parse_bulk_keys(keys_text)]
    batch_error = enforce_batch_size(len(entries))
    if batch_error:
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {"request": request, "results": [], "error": batch_error},
        )

    limiter = get_submit_rate_limiter()
    if entries and not limiter.allow(_client_id(request), len(entries)):
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {
                "request": request,
                "results": [],
                "error": f"Rate limited. Max {limiter.limit} keys per minute.",
            },
        )

    results: list[dict[str, Any]] = []
    for entry in entries:
        item = await ingest_upstream_key(
            db_path,
            entry,
            chosen_provider=provider_id,
            custom_base_url=custom_base_url.strip() or None,
        )
        if item["status"] in {"registered", "updated"} and item.get("id"):
            _schedule_recheck(item["id"], db_path)
        display_status = item["status"]
        if display_status == "registered":
            display_status = "pending_validation"
        elif display_status == "updated":
            display_status = "pending_validation"
        results.append(
            {
                "id": item.get("id"),
                "key_masked": item.get("key_masked"),
                "provider_id": item.get("provider_id"),
                "provider_display_name": item.get("provider_display_name"),
                "status": display_status,
                "error": item.get("error"),
            }
        )

    provision_results: list[dict[str, Any]] = []
    if provision_routing.lower() in {"true", "1", "yes"}:
        routable_ids = {
            str(item["provider_id"])
            for item in results
            if item.get("provider_id") and item.get("status") != "rejected"
        }
        provision_results = await ensure_routing_providers(
            db_path,
            routable_ids,
            custom_base_url=custom_base_url.strip() or None,
        )
        from janus.dashboard.reload import reload_providers

        await reload_providers(request.app)

    return _templates.TemplateResponse(
        request,
        "inventory_add_results.html",
        {
            "request": request,
            "results": results,
            "error": None,
            "provision_results": provision_results,
            "has_pending": any(item.get("status") == "pending_validation" for item in results),
            "poll_query": _poll_query(key_ids=[item["id"] for item in results if item.get("id")]),
        },
    )


@router.post("/api/inventory/keys", response_class=HTMLResponse)
async def api_submit_upstream_keys(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
    filter_provider_id: str = Form(""),
    filter_status: str = Form(""),
    filter_search: str = Form(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    entries = [
        KeyIngestEntry(key=entry["key"], label=entry["label"] or None)
        for entry in _parse_bulk_keys(keys_text)
    ]
    if not entries:
        return await _keys_partial(request, db_path, submit_message="No keys found in input.")

    batch_error = enforce_batch_size(len(entries))
    if batch_error:
        return await _keys_partial(request, db_path, submit_message=batch_error)

    limiter = get_submit_rate_limiter()
    if not limiter.allow(_client_id(request), len(entries)):
        return await _keys_partial(
            request,
            db_path,
            submit_message=f"Rate limited. Max {limiter.limit} keys per minute.",
        )

    created = 0
    for entry in entries:
        item = await ingest_upstream_key(
            db_path,
            entry,
            chosen_provider=provider_id,
            custom_base_url=custom_base_url.strip() or None,
        )
        if item["status"] in {"registered", "updated"} and item.get("id"):
            _schedule_recheck(item["id"], db_path)
            created += 1

    message = f"Added {created} key(s). Validation running in background."
    return await _keys_partial(
        request,
        db_path,
        provider_id=filter_provider_id,
        status=filter_status,
        search=filter_search,
        submit_message=message,
    )


@router.post("/api/inventory/keys/{key_id}/recheck", response_class=HTMLResponse)
async def api_recheck_upstream_key(
    request: Request,
    key_id: str,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    _schedule_recheck(key_id, db_path)
    return await _keys_partial(
        request,
        db_path,
        provider_id=provider_id,
        status=status,
        search=search,
        sort=sort,
        direction=dir,
        limit=limit,
        offset=offset,
        submit_message="Recheck started — status will update automatically.",
    )


@router.delete("/api/inventory/keys/{key_id}", response_class=HTMLResponse)
async def api_delete_upstream_key(
    request: Request,
    key_id: str,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    await delete_upstream_key(db_path, key_id)
    return await _keys_partial(
        request,
        db_path,
        provider_id=provider_id,
        status=status,
        search=search,
        sort=sort,
        direction=dir,
        limit=limit,
        offset=offset,
    )


@router.post("/api/inventory/recheck-all", response_class=HTMLResponse)
async def api_recheck_all_upstream_keys(
    request: Request,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    keys = await list_upstream_keys(db_path)
    _schedule_recheck_all(db_path)
    return await _keys_partial(
        request,
        db_path,
        provider_id=provider_id,
        status=status,
        search=search,
        sort=sort,
        direction=dir,
        limit=limit,
        offset=offset,
        submit_message=f"Rechecking {len(keys)} key(s) in background.",
    )


@router.get("/api/inventory/submit/status", response_class=HTMLResponse)
async def api_inventory_submit_status(
    request: Request,
    ids: str = Query(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    key_ids = [item for item in ids.split(",") if item]
    keys = await get_upstream_keys_by_ids(db_path, key_ids)
    provider_names = {
        p["id"]: p["display_name"]
        for p in await list_inventory_providers(db_path, active_only=True)
    }
    results = [
        {
            "id": key["id"],
            "key_masked": key["key_masked"],
            "provider_id": key["provider_id"],
            "provider_display_name": provider_names.get(key["provider_id"], key["provider_id"]),
            "status": key["status"],
            "error": key.get("last_error"),
        }
        for key in keys
    ]
    has_pending = any(item["status"] == "pending_validation" for item in results)
    return _templates.TemplateResponse(
        request,
        "inventory_add_results.html",
        {
            "request": request,
            "results": results,
            "error": None,
            "has_pending": has_pending,
            "poll_query": _poll_query(key_ids=key_ids),
        },
    )


@router.post("/api/inventory/reclassify", response_model=None)
async def api_reclassify_upstream_keys(
    request: Request,
    dry: bool = Query(default=True),
    scope: str = Query(default="invalid"),
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> Response:
    db_path = await _ensure_db(request)
    if scope not in {"invalid", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'invalid' or 'all'")
    payload = await reclassify_upstream_keys(db_path, dry_run=dry, scope=scope)
    if request.headers.get("HX-Request"):
        if dry:
            return _templates.TemplateResponse(
                request,
                "inventory_reclassify_partial.html",
                {
                    "request": request,
                    "payload": payload,
                    "poll_query": _poll_query(
                        provider_id=provider_id,
                        status=status,
                        search=search,
                        sort=sort,
                        direction=dir,
                        limit=limit,
                        offset=offset,
                    ),
                    "provider_id": provider_id,
                    "status": status,
                    "search": search,
                },
            )
        moved_count = len(payload["moved"])
        message = (
            f"Re-identified {moved_count} key(s). Re-validation running in background."
            if moved_count
            else "No keys were reassigned."
        )
        return await _keys_partial(
            request,
            db_path,
            provider_id=provider_id,
            status=status,
            search=search,
            sort=sort,
            direction=dir,
            limit=limit,
            offset=offset,
            submit_message=message,
        )
    return JSONResponse(payload)


@router.get("/api/inventory/reclassify/clear", response_class=HTMLResponse)
async def api_reclassify_clear() -> HTMLResponse:
    return HTMLResponse("")


@router.post("/api/inventory/encrypt-keys", response_class=HTMLResponse)
async def api_inventory_encrypt_keys(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    error: str | None = None
    converted = 0
    if not encryption_enabled():
        error = "Set INVENTORY_ENCRYPTION_KEY in the environment before encrypting keys."
    else:
        try:
            converted = await reencrypt_plaintext_upstream_keys(db_path)
        except RuntimeError as exc:
            error = str(exc)
    context = {
        "request": request,
        "error": error,
        "converted": converted,
        **await _encryption_context(db_path),
    }
    return _templates.TemplateResponse(request, "inventory_encryption_partial.html", context)


@router.post("/api/inventory/import", response_class=HTMLResponse)
async def api_inventory_import(
    request: Request,
    export_file: UploadFile = File(...),
    verify: str = Form(""),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    data = await export_file.read()
    error: str | None = None
    imported = 0
    try:
        imported = await import_dashboard_json(db_path, data, dry_run=False)
    except (ValueError, json.JSONDecodeError) as exc:
        error = str(exc)

    verification: dict[str, Any] | None = None
    if error is None and verify.lower() in {"true", "1", "on", "yes"}:
        verification = await verify_inventory(db_path)

    return _templates.TemplateResponse(
        request,
        "inventory_import_results.html",
        {
            "request": request,
            "error": error,
            "imported_count": imported if error is None else None,
            "verification": verification,
            "filename": export_file.filename,
        },
    )


@router.get("/api/inventory/export")
async def api_export_upstream_keys(
    request: Request,
    provider_id: str | None = None,
) -> JSONResponse:
    db_path = await _ensure_db(request)
    exported = await export_upstream_keys(db_path)
    if provider_id:
        exported = [item for item in exported if item["provider_id"] == provider_id]
    filename = "janus-inventory-export.json"
    if provider_id:
        filename = f"janus-inventory-{provider_id}.json"
    return JSONResponse(
        {
            "exported_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "count": len(exported),
            "keys": exported,
        },
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/inventory/keys")
async def api_list_upstream_keys_json(
    request: Request,
    provider_id: str = "",
    status: str = "",
    search: str = "",
    sort: str = "credits",
    dir: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> JSONResponse:
    db_path = await _ensure_db(request)
    page_size = _clamp_page_size(limit)
    total = await count_upstream_keys_filtered(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
    )
    keys = await list_upstream_keys_page(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
        sort=sort,
        direction=dir,
        limit=page_size,
        offset=offset,
        masked=True,
    )
    providers = await list_inventory_providers(db_path, active_only=True)
    return JSONResponse(
        {
            "keys": keys,
            "total": total,
            "limit": page_size,
            "offset": offset,
            "providers": providers,
        }
    )


@router.get("/api/inventory/keys/{key_id}")
async def api_get_upstream_key_json(request: Request, key_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    detail = await get_upstream_key_detail(db_path, key_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Key not found")
    models = await list_models_for_key(db_path, key_id)
    history = await list_upstream_key_history(db_path, key_id)
    for model in models:
        model["capabilities"] = _safe_json_field(model.get("capabilities"))
        model["benchmarks"] = _safe_json_field(model.get("benchmarks"))
    detail["health_warnings"] = _safe_json_field(detail.get("health_warnings"))
    detail["metadata"] = _safe_json_field(detail.get("metadata"))
    return JSONResponse(
        {
            **detail,
            "models": models,
            "history": history,
        }
    )


@router.get("/api/inventory/keys/{key_id}/partial", response_class=HTMLResponse)
async def api_upstream_key_detail_partial(request: Request, key_id: str) -> HTMLResponse:
    db_path = await _ensure_db(request)
    detail = await get_upstream_key_detail(db_path, key_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Key not found")
    models = await list_models_for_key(db_path, key_id)
    history = await list_upstream_key_history(db_path, key_id)
    for model in models:
        model["capabilities"] = _safe_json_field(model.get("capabilities"))
        model["benchmarks"] = _safe_json_field(model.get("benchmarks"))
    detail["health_warnings"] = _safe_json_field(detail.get("health_warnings"))
    detail["metadata"] = _safe_json_field(detail.get("metadata"))
    return _templates.TemplateResponse(
        request,
        "inventory_key_detail_partial.html",
        {
            "request": request,
            "key": detail,
            "models": models,
            "history": history,
            "status_badge": _status_badge,
        },
    )


@router.get("/api/inventory/keys/{key_id}/json")
async def api_upstream_key_agent_json(request: Request, key_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    detail = await get_upstream_key_detail(db_path, key_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Key not found")
    models = await list_models_for_key(db_path, key_id)
    payload = {
        "id": detail["id"],
        "provider_id": detail["provider_id"],
        "provider_display_name": detail.get("provider_display_name"),
        "key_label": detail.get("key_label"),
        "key_value": detail.get("key_value"),
        "custom_base_url": detail.get("custom_base_url"),
        "status": detail.get("status"),
        "models": [model.get("model_id") for model in models if model.get("model_id")],
        "model_details": models,
    }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="janus-key-{key_id}.json"'},
    )


@router.get("/api/inventory/best-keys")
async def api_best_upstream_keys(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    best_keys = await get_best_upstream_keys(db_path)
    return JSONResponse({"bestKeys": best_keys})


@router.get("/api/inventory/best-keys/partial", response_class=HTMLResponse)
async def api_best_upstream_keys_partial(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    return _templates.TemplateResponse(
        request,
        "inventory_best_keys_partial.html",
        {
            "request": request,
            "best_keys": await get_best_upstream_keys(db_path),
        },
    )


@router.get("/api/inventory/providers")
async def api_list_inventory_providers(request: Request) -> JSONResponse:
    db_path = await _ensure_db(request)
    providers = await list_inventory_providers(db_path, active_only=True)
    return JSONResponse({"providers": providers, "catalog": get_inventory_providers()})
