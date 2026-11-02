from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from janus.routing.inventory_bridge import inventory_provider_id_for_prefix

from .database import get_connection
from .request_logs import get_request_log

DEFAULT_ATTEMPT_LIMIT = 25
MAX_ATTEMPT_LIMIT = 50


def mask_account_reference(value: Any) -> str:
    account = str(value or "")
    if not account:
        return "Unknown account"
    if len(account) <= 4:
        return "••••"
    return f"{account[:3]}…{account[-3:]}"


def _mask_provider_reference(value: Any, account_ids: set[str]) -> str | None:
    provider = str(value or "")
    base = provider.split("::", 1)[0]
    if not base:
        return None
    if provider in account_ids or base in account_ids:
        return mask_account_reference(base)
    return base


def _mask_account_text(value: Any, account_ids: set[str]) -> Any:
    if not isinstance(value, str):
        return value
    masked = value
    for account_id in sorted(account_ids, key=len, reverse=True):
        if account_id:
            masked = masked.replace(account_id, mask_account_reference(account_id))
    return re.sub(
        r"([:;]\s+)([^:;\s]+):",
        lambda match: f"{match.group(1)}{mask_account_reference(match.group(2))}:",
        masked,
    )


def _failure_class(row: dict[str, Any]) -> str | None:
    recorded = row.get("failure_class")
    if recorded:
        return str(recorded)
    outcome = row.get("outcome")
    if outcome == "aborted":
        return "cancelled"
    if outcome not in {"error", "client_error"}:
        return None
    status = row.get("status")
    if status is None:
        return "network"
    code = int(status)
    if code == 429:
        return "rate_limit"
    if code in (401, 403):
        return "auth_error"
    if code == 402:
        return "payment_error"
    if code >= 500:
        return "server_error"
    if code >= 400:
        return "client_error"
    return "unknown"


def _final_outcome(status: Any, attempt_count: int, last_attempt: str | None) -> str:
    code = int(status or 0)
    if code == 499:
        return "cancelled"
    if attempt_count and last_attempt == "client_error":
        return "upstream_client_error"
    if attempt_count and last_attempt == "error" and 200 <= code < 400:
        return "upstream_failure"
    if 200 <= code < 400:
        return "success"
    if code == 400:
        return "client_validation"
    if 400 <= code < 500:
        return "client_rejected"
    if code >= 500:
        return "upstream_failure"
    return "unknown"


def _recovery_action(failure_class: str | None, inventory_id: str | None) -> dict[str, str] | None:
    if failure_class in {None, "cancelled"}:
        return None
    labels = {
        "auth_error": "Review credentials",
        "payment_error": "Review billing and quota",
        "rate_limit": "Review account limits",
    }
    label = labels.get(failure_class, "Review account health")
    query = f"?{urlencode({'provider_id': inventory_id})}" if inventory_id else ""
    return {
        "label": label,
        "href": f"/dashboard/ui/inventory/keys{query}",
    }


async def get_request_timeline(
    db_path: str | Path,
    log_id: int,
    *,
    limit: int = DEFAULT_ATTEMPT_LIMIT,
    offset: int = 0,
) -> dict[str, Any] | None:
    log = await get_request_log(db_path, log_id)
    if log is None:
        return None
    limit = max(1, min(limit, MAX_ATTEMPT_LIMIT))
    offset = max(0, offset)
    request_id = log.get("request_id")
    log_account = str(log.get("account_id") or "")
    base: dict[str, Any] = {
        **log,
        "provider_id": _mask_provider_reference(log.get("provider_id"), {log_account}),
        "account_id": mask_account_reference(log_account) if log_account else None,
        "has_bodies": bool(log.get("request_body") or log.get("response_body")),
        "correlation": {"available": bool(request_id), "request_id": request_id},
        "outcome": None,
        "attempts": [],
        "attempt_pagination": {
            "total": 0,
            "limit": limit,
            "offset": 0,
            "page": 1,
            "total_pages": 1,
        },
    }
    if not request_id:
        base["error"] = _mask_account_text(base.get("error"), {log_account})
        base["final_outcome"] = _final_outcome(log.get("status"), 0, None)
        return base

    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT timestamp, model, provider_id, account_id, status, duration_ms,
                      streamed, attempts
               FROM request_outcomes WHERE request_id = ? ORDER BY id DESC LIMIT 1""",
            (request_id,),
        ) as cur:
            outcome_row = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) AS n FROM attempt_signals WHERE request_id = ?", (request_id,)
        ) as cur:
            count_row = await cur.fetchone()
        total = int(count_row["n"]) if count_row else 0
        if total and offset >= total:
            offset = max(0, ((total - 1) // limit) * limit)
        async with db.execute(
            """SELECT outcome FROM attempt_signals
               WHERE request_id = ? ORDER BY attempt_order DESC, id DESC LIMIT 1""",
            (request_id,),
        ) as cur:
            last_row = await cur.fetchone()
        async with db.execute(
            """SELECT id, timestamp, attempt_order, model, provider_id, account_id, outcome,
                      status, ttft_ms, duration_ms, output_tokens, failure_class,
                      cooldown_decision, cooldown_seconds
               FROM attempt_signals WHERE request_id = ?
               ORDER BY attempt_order ASC, id ASC LIMIT ? OFFSET ?""",
            (request_id, limit, offset),
        ) as cur:
            attempt_rows = [dict(row) for row in await cur.fetchall()]

        async with db.execute(
            """SELECT DISTINCT account_id, provider_id FROM attempt_signals
               WHERE request_id = ? LIMIT 101""",
            (request_id,),
        ) as cur:
            reference_rows = [dict(row) for row in await cur.fetchall()]
        account_ids = {log_account} if log_account else set()
        if outcome_row and outcome_row["account_id"]:
            account_ids.add(str(outcome_row["account_id"]))
        account_ids.update(
            str(row["account_id"]) for row in reference_rows if row.get("account_id")
        )
        config_ids = {
            str(row["provider_id"]).split("::", 1)[0]
            for row in reference_rows
            if row.get("provider_id")
        }
        config_ids.update(account_ids)
        account_providers: dict[str, str] = {}
        config_prefixes: dict[str, str] = {}
        async with db.execute("SELECT id FROM inventory_providers WHERE is_active = 1") as cur:
            active_inventory_ids = {str(row[0]) for row in await cur.fetchall()}
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            async with db.execute(
                f"SELECT id, provider_id FROM upstream_keys WHERE id IN ({placeholders})",
                tuple(sorted(account_ids)),
            ) as cur:
                account_providers = {
                    str(row["id"]): str(row["provider_id"]) for row in await cur.fetchall()
                }
        if config_ids:
            placeholders = ",".join("?" for _ in config_ids)
            async with db.execute(
                f"SELECT id, prefix FROM providers WHERE id IN ({placeholders})",
                tuple(sorted(config_ids)),
            ) as cur:
                config_prefixes = {
                    str(row["id"]): str(row["prefix"]) for row in await cur.fetchall()
                }

    attempts: list[dict[str, Any]] = []
    for index, row in enumerate(attempt_rows, start=offset + 1):
        account_id = str(row.get("account_id") or "")
        provider_id = str(row.get("provider_id") or "")
        config_id = provider_id.split("::", 1)[0]
        inventory_id = account_providers.get(account_id)
        if inventory_id is None:
            prefix = config_prefixes.get(account_id) or config_prefixes.get(config_id)
            if prefix:
                inventory_id = inventory_provider_id_for_prefix(prefix)
        if inventory_id not in active_inventory_ids:
            inventory_id = None
        failure_class = _failure_class(row)
        attempts.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "attempt_order": row.get("attempt_order") or index,
                "model": row.get("model"),
                "provider_id": _mask_provider_reference(provider_id, account_ids),
                "account": mask_account_reference(account_id),
                "outcome": row["outcome"],
                "status": row.get("status"),
                "ttft_ms": row.get("ttft_ms"),
                "duration_ms": row.get("duration_ms"),
                "output_tokens": row.get("output_tokens"),
                "failure_class": failure_class,
                "cooldown": {
                    "decision": row.get("cooldown_decision") or "none",
                    "seconds": row.get("cooldown_seconds"),
                },
                "recovery": _recovery_action(failure_class, inventory_id),
            }
        )

    last_attempt = str(last_row["outcome"]) if last_row else None
    outcome = dict(outcome_row) if outcome_row else None
    status = outcome.get("status") if outcome else log.get("status")
    kind = _final_outcome(status, total, last_attempt)
    if outcome is not None:
        outcome["provider_id"] = _mask_provider_reference(outcome.get("provider_id"), account_ids)
        outcome.pop("account_id", None)
        outcome["kind"] = kind
    base["provider_id"] = _mask_provider_reference(log.get("provider_id"), account_ids)
    base["error"] = _mask_account_text(base.get("error"), account_ids)
    base.update(
        {
            "outcome": outcome,
            "attempts": attempts,
            "attempt_pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "page": (offset // limit) + 1,
                "total_pages": max(1, (total + limit - 1) // limit),
            },
            "final_outcome": kind,
        }
    )
    return base
