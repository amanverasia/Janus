from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Request

from janus.storage.api_keys import list_keys
from janus.storage.budgets import get_budget_status, get_budgets
from janus.storage.cooldowns import get_active_cooldowns
from janus.storage.database import get_connection
from janus.storage.providers_db import list_providers
from janus.storage.usage import get_unpriced_models

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "critical"]
Summary = Literal["ok", "warning", "critical"]

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
_ALERT_CAP = 8
_LOW_CREDIT_USD = 1.0
_BAD_INVENTORY_STATUSES = frozenset({"critical", "exhausted", "invalid"})


@dataclass(frozen=True)
class DashboardAlert:
    id: str
    severity: Severity
    title: str
    detail: str
    href: str


async def collect_dashboard_alerts(db_path: Path, request: Request) -> dict[str, Any]:
    alerts: list[DashboardAlert] = []
    for collector in (
        _budget_alerts,
        _quota_alerts,
        _cooldown_alerts,
        _inventory_alerts,
        _unpriced_alerts,
        _setup_alerts,
    ):
        try:
            alerts.extend(await collector(db_path, request))
        except Exception:
            logger.exception("Dashboard alert collector %s failed", collector.__name__)
    alerts.sort(key=lambda a: (_SEVERITY_RANK[a.severity], a.id))
    alerts = alerts[:_ALERT_CAP]
    summary = _summarize(alerts)
    counts = {
        "critical": sum(1 for a in alerts if a.severity == "critical"),
        "warning": sum(1 for a in alerts if a.severity == "warning"),
    }
    return {"alerts": alerts, "summary": summary, "counts": counts}


def _summarize(alerts: list[DashboardAlert]) -> Summary:
    if any(a.severity == "critical" for a in alerts):
        return "critical"
    if any(a.severity == "warning" for a in alerts):
        return "warning"
    return "ok"


async def _budget_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    del request
    alerts: list[DashboardAlert] = []
    keys = await list_keys(db_path)
    key_names = {int(k["id"]): str(k["name"]) for k in keys if k.get("is_active", 1)}

    budgets = await get_budgets(db_path)
    seen_key_ids: set[int | None] = set()
    for budget in budgets:
        key_id = budget["key_id"]
        if key_id in seen_key_ids:
            continue
        seen_key_ids.add(key_id)
        status = await get_budget_status(db_path, key_id=key_id)
        if status is None:
            continue
        budget_status = status["status"]
        if budget_status not in ("warning", "exceeded"):
            continue
        pct_used = status["pct_used"]
        if key_id is None:
            alert_id = "budget:global"
            title = "Global daily budget"
            detail = (
                f"Spend is at {pct_used:.0f}% of the daily limit "
                f"(${status['today_spend']:.2f} / ${status['daily_limit']:.2f})."
            )
        else:
            key_name = key_names.get(int(key_id), f"Key #{key_id}")
            alert_id = f"budget:key:{key_id}"
            title = f"Budget for {key_name}"
            detail = (
                f"{key_name} is at {pct_used:.0f}% of its daily limit "
                f"(${status['today_spend']:.2f} / ${status['daily_limit']:.2f})."
            )
        severity: Severity = "critical" if budget_status == "exceeded" else "warning"
        alerts.append(
            DashboardAlert(
                id=alert_id,
                severity=severity,
                title=title,
                detail=detail,
                href="/dashboard/budgets",
            )
        )

    if None not in seen_key_ids:
        status = await get_budget_status(db_path, key_id=None)
        if status is not None and status["status"] in ("warning", "exceeded"):
            pct_used = status["pct_used"]
            severity = "critical" if status["status"] == "exceeded" else "warning"
            alerts.append(
                DashboardAlert(
                    id="budget:global",
                    severity=severity,
                    title="Global daily budget",
                    detail=(
                        f"Spend is at {pct_used:.0f}% of the daily limit "
                        f"(${status['today_spend']:.2f} / ${status['daily_limit']:.2f})."
                    ),
                    href="/dashboard/budgets",
                )
            )

    return alerts


async def _quota_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    del request
    from janus.storage.quotas import describe_reset, get_window_usage, quota_status

    alerts: list[DashboardAlert] = []
    providers = await list_providers(db_path, enabled_only=True)
    for provider in providers:
        if not provider.get("quota_window") or not provider.get("quota_limit"):
            continue
        usage = await get_window_usage(db_path, str(provider["id"]), str(provider["quota_window"]))
        metric = provider.get("quota_metric") or "requests"
        used = usage["tokens"] if metric == "tokens" else usage["requests"]
        limit = int(provider["quota_limit"])
        status = quota_status(used, limit)
        if status not in ("warning", "exhausted"):
            continue
        reset = describe_reset(str(provider["quota_window"]))
        prefix = str(provider.get("prefix") or provider["id"])
        if status == "exhausted":
            severity: Severity = "critical"
            title = f"Quota exhausted for {prefix}"
            detail = f"Provider {prefix} has used {used:,} of {limit:,} {metric}."
        else:
            severity = "warning"
            title = f"Quota warning for {prefix}"
            pct = min(round(used * 100 / limit), 100) if limit else 0
            detail = (
                f"Provider {prefix} is at {pct}% of its {provider['quota_window']} "
                f"quota ({used:,} / {limit:,} {metric})."
            )
        if reset.get("resets_in"):
            detail = f"{detail} Resets in {reset['resets_in']}."
        alerts.append(
            DashboardAlert(
                id=f"quota:{provider['id']}",
                severity=severity,
                title=title,
                detail=detail,
                href="/dashboard/providers",
            )
        )
    return alerts


async def _cooldown_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    del request
    from janus.storage.settings import cooldowns_enabled, get_all_settings

    settings = await get_all_settings(db_path)
    if not cooldowns_enabled(settings):
        return [
            DashboardAlert(
                id="cooldown:disabled",
                severity="info",
                title="Account cooldowns disabled",
                detail="Routing will retry cooled-down accounts immediately.",
                href="/dashboard/settings",
            )
        ]
    cooldowns = await get_active_cooldowns(db_path)
    if not cooldowns:
        return []
    count = len(cooldowns)
    noun = "account" if count == 1 else "accounts"
    return [
        DashboardAlert(
            id="cooldown:active",
            severity="warning",
            title="Routing cooldowns active",
            detail=f"{count} upstream {noun} on cooldown after recent errors.",
            href="/dashboard/routing",
        )
    ]


async def _inventory_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    del request
    alerts: list[DashboardAlert] = []
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT k.status, k.credits_remaining, p.billing_model
               FROM upstream_keys k
               JOIN inventory_providers p ON k.provider_id = p.id
               WHERE k.status != 'revoked'"""
        ) as cur:
            rows = await cur.fetchall()

    bad_status_count = 0
    low_credit_count = 0
    for row in rows:
        billing_model = str(row["billing_model"] or "")
        if billing_model == "subscription":
            continue
        status = str(row["status"] or "")
        if status in _BAD_INVENTORY_STATUSES:
            bad_status_count += 1
        credits = row["credits_remaining"]
        if credits is not None and float(credits) < _LOW_CREDIT_USD:
            low_credit_count += 1

    if bad_status_count:
        noun = "key" if bad_status_count == 1 else "keys"
        alerts.append(
            DashboardAlert(
                id="inventory:unhealthy",
                severity="critical",
                title="Upstream keys need attention",
                detail=(
                    f"{bad_status_count} inventory {noun} in critical, exhausted, or invalid state."
                ),
                href="/dashboard/inventory/keys",
            )
        )
    if low_credit_count:
        noun = "key" if low_credit_count == 1 else "keys"
        alerts.append(
            DashboardAlert(
                id="inventory:low_credits",
                severity="warning",
                title="Low upstream credits",
                detail=(
                    f"{low_credit_count} inventory {noun} below ${_LOW_CREDIT_USD:.2f} remaining."
                ),
                href="/dashboard/inventory/keys",
            )
        )
    return alerts


async def _unpriced_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    registry = getattr(request.app.state, "pricing_registry", None)
    if registry is None:
        return []
    models = await get_unpriced_models(db_path, days=30)
    missing = [row for row in models if registry.get(str(row["model"])) is None]
    if not missing:
        return []
    count = len(missing)
    noun = "model" if count == 1 else "models"
    top = ", ".join(str(row["model"]) for row in missing[:3])
    if count > 3:
        top = f"{top}, and {count - 3} more"
    return [
        DashboardAlert(
            id="unpriced:models",
            severity="warning",
            title="Unpriced models in usage",
            detail=f"{count} {noun} have token usage but no pricing entry ({top}).",
            href="/dashboard/pricing",
        )
    ]


async def _setup_alerts(db_path: Path, request: Request) -> list[DashboardAlert]:
    del request
    alerts: list[DashboardAlert] = []
    enabled_providers = await list_providers(db_path, enabled_only=True)
    if not enabled_providers:
        alerts.append(
            DashboardAlert(
                id="setup:no_providers",
                severity="critical",
                title="No enabled providers",
                detail="Add and enable at least one provider before routing traffic.",
                href="/dashboard/providers",
            )
        )
    keys = await list_keys(db_path)
    active_keys = [k for k in keys if k.get("is_active", 1)]
    if not active_keys:
        alerts.append(
            DashboardAlert(
                id="setup:no_api_keys",
                severity="info",
                title="No Janus API keys",
                detail="Create a Janus API key for clients to authenticate.",
                href="/dashboard/keys",
            )
        )
    return alerts
