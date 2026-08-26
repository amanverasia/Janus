from __future__ import annotations

import asyncio
import json
import logging
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
from janus.inventory.key_checker import check_all_upstream_keys, check_upstream_key
from janus.inventory.key_encryption import CredentialEncryptionError, encryption_enabled
from janus.inventory.migrate import import_dashboard_json_with_ids, verify_inventory
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
from janus.storage.providers_db import (
    count_provider_encryption_state,
    list_providers,
    reencrypt_plaintext_provider_keys,
)
from janus.storage.upstream_keys import (
    DEFAULT_PAGE_SIZE,
    SORT_COLUMNS,
    archive_upstream_keys,
    count_pending_upstream_keys,
    count_storage_encryption_state,
    count_upstream_keys_filtered,
    delete_upstream_key,
    delete_upstream_keys,
    export_upstream_keys,
    get_upstream_key,
    get_upstream_key_detail,
    get_upstream_keys_by_ids,
    list_upstream_key_history,
    list_upstream_key_ids_filtered,
    list_upstream_keys,
    list_upstream_keys_page,
    reencrypt_plaintext_upstream_keys,
    update_upstream_key,
)
from janus.storage.upstream_models import list_models_for_key

router = APIRouter(dependencies=[Depends(require_dashboard_access)])
logger = logging.getLogger(__name__)
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _schedule_recheck(key_id: str, db_path: Path) -> None:
    schedule_upstream_recheck(key_id, db_path)


def _schedule_recheck_all(db_path: Path) -> None:
    asyncio.create_task(_run_all_keys(db_path))


async def _run_all_keys(db_path: Path) -> None:
    try:
        keys = await list_upstream_keys(db_path)
        for key in keys:
            await update_upstream_key(
                db_path,
                key["id"],
                {
                    "status": "pending_validation",
                    "last_error": None,
                    "consecutive_failures": 0,
                    "validation_paused_at": None,
                },
            )
        await check_all_upstream_keys(db_path)
    except Exception:
        logger.exception("Inventory recheck-all task failed")


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


def _wants_json(request: Request) -> bool:
    for entry in request.headers.get("accept", "").split(","):
        media_type = entry.split(";", maxsplit=1)[0].strip().lower()
        if media_type == "application/json" or media_type.endswith("+json"):
            return True
    return False


def _json_error(message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
        headers=_NO_STORE_HEADERS,
    )


def _safe_ingest_error(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    lowered = value.lower()
    if "too short" in lowered:
        return "Credential is too short."
    if "too long" in lowered:
        return "Credential is too long."
    if "does not look like" in lowered:
        return "Value does not look like a credential."
    if "base_url" in lowered or "base url" in lowered:
        return "Base URL must be a valid HTTP(S) URL."
    if "unknown provider" in lowered:
        return "Provider is not recognized."
    if "access token" in lowered:
        return "Credential is missing an access token."
    if "json" in lowered:
        return "Credential JSON is invalid."
    if "expires" in lowered or "expiry" in lowered:
        return "Credential expiry is invalid."
    if "missing" in lowered:
        return "Credential is missing."
    return "Credential was rejected."


def _safe_submit_result(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "rejected")
    key_masked = item.get("key_masked")
    if status == "rejected" or not isinstance(key_masked, str):
        key_masked = "****"
    return {
        "id": str(item["id"]) if item.get("id") is not None else None,
        "key_masked": key_masked,
        "provider_id": (str(item["provider_id"]) if item.get("provider_id") is not None else None),
        "provider_display_name": (
            str(item["provider_display_name"])
            if item.get("provider_display_name") is not None
            else None
        ),
        "status": status,
        "error": _safe_ingest_error(item.get("error")),
    }


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
    archived_view = status == "archived"
    total = await count_upstream_keys_filtered(
        db_path,
        provider_id=provider_id or None,
        status=status or None,
        search=search or None,
        include_archived=archived_view,
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
        include_archived=archived_view,
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
    text = raw.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            from janus.inventory.codex_credentials import expand_codex_paste

            expanded = expand_codex_paste(text)
            if expanded:
                return [{"label": e.get("label") or "", "key": e["key"]} for e in expanded]
        except ValueError:
            pass
        try:
            json.loads(text)
            return [{"label": "", "key": text}]
        except json.JSONDecodeError:
            pass
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
        "validation_paused": "bg-gray-700 text-gray-200",
        "pending_validation": "bg-yellow-900 text-yellow-200",
        "error": "bg-orange-900 text-orange-200",
        "daily_exhausted": "bg-purple-900 text-purple-200",
    }
    return mapping.get(status, "bg-gray-700 text-gray-200")


async def _encryption_context(db_path: Path) -> dict[str, Any]:
    return {
        "encryption": await count_storage_encryption_state(db_path),
        "provider_encryption": await count_provider_encryption_state(db_path),
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
    context["is_partial"] = False
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
    context["is_partial"] = True
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


@router.post("/api/inventory/submit", response_class=HTMLResponse, response_model=None)
async def api_inventory_submit(
    request: Request,
    keys_text: str = Form(...),
    provider_id: str = Form("auto"),
    custom_base_url: str = Form(""),
    provision_routing: str = Form("false"),
) -> Response:
    db_path = await _ensure_db(request)
    entries = [KeyIngestEntry(key=entry["key"]) for entry in _parse_bulk_keys(keys_text)]
    batch_error = enforce_batch_size(len(entries))
    if batch_error:
        if _wants_json(request):
            return _json_error(batch_error, status_code=422)
        return _templates.TemplateResponse(
            request,
            "inventory_add_results.html",
            {"request": request, "results": [], "error": batch_error},
        )
    if not entries and _wants_json(request):
        return _json_error("No credentials found in input.", status_code=422)

    limiter = get_submit_rate_limiter()
    if entries and not limiter.allow(_client_id(request), len(entries)):
        error = f"Rate limited. Max {limiter.limit} credentials per minute."
        if _wants_json(request):
            return _json_error(error, status_code=429)
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

    if _wants_json(request):
        safe_results = [_safe_submit_result(item) for item in results]
        rejected_count = sum(item["status"] == "rejected" for item in safe_results)
        accepted_count = len(safe_results) - rejected_count
        queued_count = sum(item["status"] == "pending_validation" for item in safe_results)
        payload: dict[str, Any] = {
            "ok": rejected_count == 0,
            "processed_count": len(safe_results),
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "queued_count": queued_count,
            "results": safe_results,
            "provision_results": provision_results,
            "has_pending": queued_count > 0,
        }
        return JSONResponse(
            payload,
            status_code=422 if rejected_count and accepted_count == 0 else 200,
            headers=_NO_STORE_HEADERS,
        )

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


@router.post("/api/inventory/keys/bulk/archive", response_class=HTMLResponse)
async def api_bulk_archive_upstream_keys(
    request: Request,
    key_ids: str = Form(""),
    apply_to_all: str = Form(""),
    action: str = Form("archive"),
    provider_id: str = Form(""),
    status: str = Form(""),
    search: str = Form(""),
    sort: str = Form("credits"),
    dir: str = Form("desc"),
    limit: int = Form(DEFAULT_PAGE_SIZE),
    offset: int = Form(0),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    ids = await _resolve_bulk_ids(
        db_path,
        key_ids=_parse_key_ids(key_ids),
        apply_to_all=apply_to_all.lower() in {"true", "1", "yes", "on"},
        provider_id=provider_id,
        status=status,
        search=search,
    )
    archived = action.lower() != "restore"
    count = await archive_upstream_keys(db_path, ids, archived=archived)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    verb = "Archived" if archived else "Restored"
    message = f"{verb} {count} key(s)."
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


@router.post("/api/inventory/keys/bulk/recheck", response_class=HTMLResponse)
async def api_bulk_recheck_upstream_keys(
    request: Request,
    key_ids: str = Form(""),
    apply_to_all: str = Form(""),
    provider_id: str = Form(""),
    status: str = Form(""),
    search: str = Form(""),
    sort: str = Form("credits"),
    dir: str = Form("desc"),
    limit: int = Form(DEFAULT_PAGE_SIZE),
    offset: int = Form(0),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    ids = await _resolve_bulk_ids(
        db_path,
        key_ids=_parse_key_ids(key_ids),
        apply_to_all=apply_to_all.lower() in {"true", "1", "yes", "on"},
        provider_id=provider_id,
        status=status,
        search=search,
    )
    for key_id in ids:
        _schedule_recheck(key_id, db_path)
    message = f"Rechecking {len(ids)} key(s) in background."
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


@router.post("/api/inventory/keys/bulk/delete", response_class=HTMLResponse)
async def api_bulk_delete_upstream_keys(
    request: Request,
    key_ids: str = Form(""),
    apply_to_all: str = Form(""),
    provider_id: str = Form(""),
    status: str = Form(""),
    search: str = Form(""),
    sort: str = Form("credits"),
    dir: str = Form("desc"),
    limit: int = Form(DEFAULT_PAGE_SIZE),
    offset: int = Form(0),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    ids = await _resolve_bulk_ids(
        db_path,
        key_ids=_parse_key_ids(key_ids),
        apply_to_all=apply_to_all.lower() in {"true", "1", "yes", "on"},
        provider_id=provider_id,
        status=status,
        search=search,
    )
    count = await delete_upstream_keys(db_path, ids)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    message = f"Deleted {count} key(s)."
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


def _parse_key_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


async def _resolve_bulk_ids(
    db_path: Path,
    *,
    key_ids: list[str],
    apply_to_all: bool,
    provider_id: str,
    status: str,
    search: str,
) -> list[str]:
    if apply_to_all:
        archived_view = status == "archived"
        return await list_upstream_key_ids_filtered(
            db_path,
            provider_id=provider_id or None,
            status=status or None,
            search=search or None,
            include_archived=archived_view,
        )
    return key_ids


@router.post("/api/inventory/keys/{key_id}/test")
async def api_test_upstream_key(request: Request, key_id: str) -> JSONResponse:
    db_path = await _ensure_db(request)
    key = await get_upstream_key(db_path, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    await update_upstream_key(
        db_path,
        key_id,
        {
            "status": "pending_validation",
            "last_error": None,
            "consecutive_failures": 0,
            "validation_paused_at": None,
        },
    )
    await check_upstream_key(db_path, key_id)
    result = await get_upstream_key(db_path, key_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Key not found")
    is_valid = bool(result.get("is_valid"))
    message = (
        result.get("usability_note")
        or result.get("last_error")
        or ("Valid key" if is_valid else "Key is not valid")
    )
    models = await list_models_for_key(db_path, key_id)
    payload: dict[str, Any] = {
        "ok": is_valid,
        "usable": bool(result.get("is_usable")),
        "usability_status": result.get("usability_status", "unknown"),
        "message": message,
    }
    payload["models"] = len(models)
    if result.get("credits_remaining") is not None:
        payload["credits_remaining"] = result["credits_remaining"]
    return JSONResponse(payload)


@router.post("/api/inventory/keys/{key_id}/archive", response_class=HTMLResponse)
async def api_archive_upstream_key(
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
    await archive_upstream_keys(db_path, [key_id], archived=True)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
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
        submit_message="Key archived — excluded from routing.",
    )


@router.post("/api/inventory/keys/{key_id}/restore", response_class=HTMLResponse)
async def api_restore_upstream_key(
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
    await archive_upstream_keys(db_path, [key_id], archived=False)
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
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
        submit_message="Key restored.",
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


@router.post("/api/inventory/encrypt-keys", response_class=HTMLResponse, response_model=None)
async def api_inventory_encrypt_keys(request: Request) -> Response:
    db_path = await _ensure_db(request)
    error: str | None = None
    json_error: str | None = None
    upstream_converted = 0
    provider_converted = 0
    if not encryption_enabled():
        error = "Set INVENTORY_ENCRYPTION_KEY in the environment before encrypting keys."
        json_error = "Credential encryption is not configured on this Janus node."
    else:
        try:
            upstream_converted = await reencrypt_plaintext_upstream_keys(db_path)
            provider_converted = await reencrypt_plaintext_provider_keys(db_path)
        except RuntimeError as exc:
            error = str(exc)
            json_error = "Stored credentials could not be encrypted. Verify the encryption key."
    encryption_context = await _encryption_context(db_path)
    if _wants_json(request):
        payload: dict[str, Any] = {
            "ok": json_error is None,
            "upstream_converted": upstream_converted,
            "provider_converted": provider_converted,
            **encryption_context,
        }
        if json_error is not None:
            payload["error"] = json_error
        return JSONResponse(
            payload,
            status_code=409 if json_error is not None else 200,
            headers=_NO_STORE_HEADERS,
        )
    context = {
        "request": request,
        "error": error,
        "upstream_converted": upstream_converted,
        "provider_converted": provider_converted,
        **encryption_context,
    }
    return _templates.TemplateResponse(request, "inventory_encryption_partial.html", context)


@router.post("/api/inventory/import", response_class=HTMLResponse, response_model=None)
async def api_inventory_import(
    request: Request,
    export_file: UploadFile = File(...),
    verify: str = Form(""),
) -> Response:
    db_path = await _ensure_db(request)
    data = await export_file.read()
    error: str | None = None
    json_error: str | None = None
    imported = 0
    imported_ids: list[str] = []
    try:
        imported, imported_ids = await import_dashboard_json_with_ids(db_path, data, dry_run=False)
    except json.JSONDecodeError as exc:
        error = str(exc)
        json_error = "The selected file is not valid JSON."
    except ValueError as exc:
        error = str(exc)
        if error == "Expected export JSON with a top-level 'keys' array or a bare array":
            json_error = error
        else:
            json_error = "The import contains an invalid field value."
    except (TypeError, OverflowError):
        if not _wants_json(request):
            raise
        json_error = "The import contains an invalid field value."
        error = json_error
    except CredentialEncryptionError:
        if not _wants_json(request):
            raise
        json_error = "Credential storage encryption is not configured correctly."
        error = json_error

    if error is None and imported_ids:
        for key_id in imported_ids:
            await update_upstream_key(
                db_path,
                key_id,
                {
                    "status": "pending_validation",
                    "is_valid": 0,
                    "is_usable": 0,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "validation_paused_at": None,
                },
            )
            _schedule_recheck(key_id, db_path)
        from janus.dashboard.reload import reload_providers

        await reload_providers(request.app)

    verification: dict[str, Any] | None = None
    if error is None and verify.lower() in {"true", "1", "on", "yes"}:
        verification = await verify_inventory(db_path)

    if _wants_json(request):
        if json_error is not None:
            return _json_error(json_error, status_code=422)
        if imported == 0:
            return _json_error("No importable credentials were found.", status_code=422)
        return JSONResponse(
            {
                "ok": True,
                "imported_count": imported,
                "recheck_count": len(imported_ids),
                "verification": verification,
            },
            headers=_NO_STORE_HEADERS,
        )

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
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **_NO_STORE_HEADERS,
        },
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


@router.post("/api/inventory/keys/{key_id}/reveal")
async def api_reveal_upstream_key(request: Request, key_id: str) -> JSONResponse:
    try:
        db_path = await _ensure_db(request)
        detail = await get_upstream_key_detail(db_path, key_id, include_secret=True)
    except CredentialEncryptionError:
        logger.warning(
            "Credential reveal failed for key %s due to encryption configuration", key_id
        )
        return JSONResponse(
            {"detail": "Credential unavailable"},
            status_code=503,
            headers=_NO_STORE_HEADERS,
        )
    if detail is None:
        return JSONResponse(
            {"detail": "Key not found"},
            status_code=404,
            headers=_NO_STORE_HEADERS,
        )
    key_value = detail.get("key_value")
    if not isinstance(key_value, str):
        return JSONResponse(
            {"detail": "Credential unavailable"},
            status_code=503,
            headers=_NO_STORE_HEADERS,
        )
    return JSONResponse({"key_value": key_value}, headers=_NO_STORE_HEADERS)


@router.post("/api/inventory/keys/{key_id}/priority", response_class=HTMLResponse)
async def api_update_upstream_key_priority(
    request: Request,
    key_id: str,
    priority: int = Form(0),
) -> HTMLResponse:
    db_path = await _ensure_db(request)
    detail = await get_upstream_key_detail(db_path, key_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Key not found")
    await update_upstream_key(db_path, key_id, {"priority": max(0, priority)})
    from janus.dashboard.reload import reload_providers

    await reload_providers(request.app)
    return await api_upstream_key_detail_partial(request, key_id)


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
    detail = await get_upstream_key_detail(db_path, key_id, include_secret=True)
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
        headers={
            "Content-Disposition": f'attachment; filename="janus-key-{key_id}.json"',
            **_NO_STORE_HEADERS,
        },
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
