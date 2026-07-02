from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from janus.dashboard.routes import _ensure_db
from janus.inventory.ingestion import KeyIngestEntry, enforce_batch_size, ingest_upstream_key
from janus.inventory.push_auth import require_inventory_push_token
from janus.inventory.rate_limit import get_submit_rate_limiter
from janus.inventory.recheck_scheduler import schedule_upstream_recheck

router = APIRouter()


class PushKeyEntry(BaseModel):
    key: str
    label: str | None = None
    provider: str | None = None
    base_url: str | None = None


class PushRequestBody(BaseModel):
    keys: list[PushKeyEntry] | None = None
    key: str | None = None
    label: str | None = None
    provider: str | None = None
    base_url: str | None = None
    node_id: str | None = None


def _normalize_push_entries(body: PushRequestBody) -> list[KeyIngestEntry]:
    if body.keys is not None:
        return [
            KeyIngestEntry(
                key=item.key,
                label=item.label,
                provider=item.provider,
                base_url=item.base_url,
                source_node=body.node_id,
            )
            for item in body.keys
        ]
    if body.key:
        return [
            KeyIngestEntry(
                key=body.key,
                label=body.label,
                provider=body.provider,
                base_url=body.base_url,
                source_node=body.node_id,
            )
        ]
    return []


def _client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/push", dependencies=[Depends(require_inventory_push_token)])
async def api_inventory_push(request: Request, body: PushRequestBody) -> JSONResponse:
    entries = _normalize_push_entries(body)
    if not entries:
        raise HTTPException(status_code=400, detail="Provide { key } or { keys: [...] }")

    batch_error = enforce_batch_size(len(entries))
    if batch_error:
        raise HTTPException(status_code=413, detail=batch_error)

    limiter = get_submit_rate_limiter()
    if not limiter.allow(_client_id(request), len(entries)):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limited. Max {limiter.limit} keys per minute.",
        )

    db_path = await _ensure_db(request)
    results: list[dict[str, Any]] = []
    for entry in entries:
        result = await ingest_upstream_key(
            db_path,
            entry,
            require_provider=entry.provider is None,
        )
        if result["status"] in {"registered", "updated"} and result.get("id"):
            schedule_upstream_recheck(result["id"], db_path)
        results.append(result)

    summary = {
        "registered": sum(1 for item in results if item["status"] == "registered"),
        "updated": sum(1 for item in results if item["status"] == "updated"),
        "existed": sum(1 for item in results if item["status"] == "exists"),
        "rejected": sum(1 for item in results if item["status"] == "rejected"),
        "unidentified": sum(1 for item in results if item["status"] == "unidentified"),
        "total": len(entries),
    }
    status_code = 201 if summary["registered"] > 0 else 200
    return JSONResponse({"summary": summary, "results": results}, status_code=status_code)
