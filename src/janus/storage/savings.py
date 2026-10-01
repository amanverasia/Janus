from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from janus.canonical.models import Usage
from janus.pricing.calculator import compute_cost
from janus.pricing.registry import PricingRegistry
from janus.storage.settings import get_all_settings, resolve_savings_baseline
from janus.storage.time_windows import current_reporting_day
from janus.storage.usage import get_savings_window_totals

logger = logging.getLogger(__name__)

BY_MODEL_LIMIT = 25


def _baseline_cost(totals: dict[str, Any], baseline: str, registry: PricingRegistry) -> float:
    usage = Usage(
        input_tokens=int(totals["input_tokens"]),
        output_tokens=int(totals["output_tokens"]),
        cache_creation_input_tokens=int(totals["cache_creation_tokens"]),
        cache_read_input_tokens=int(totals["cache_read_tokens"]),
    )
    return compute_cost(usage, baseline, registry)


def build_savings(
    totals: dict[str, Any], *, baseline: str, registry: PricingRegistry
) -> dict[str, Any]:
    baseline_priced = registry.get(baseline) is not None
    actual_cost = sum(float(row["cost"]) for row in totals["included"])
    by_model: list[dict[str, Any]] = []
    baseline_cost = 0.0
    if baseline_priced:
        for row in totals["included"]:
            row_baseline = _baseline_cost(row, baseline, registry)
            baseline_cost += row_baseline
            by_model.append(
                {
                    "model": row["model"],
                    "requests": int(row["requests"]),
                    "actual_cost": round(float(row["cost"]), 6),
                    "baseline_cost": round(row_baseline, 6),
                    "savings": round(row_baseline - float(row["cost"]), 6),
                }
            )
        by_model.sort(key=lambda row: (-row["savings"], row["model"]))
    savings = baseline_cost - actual_cost
    return {
        "baseline": baseline,
        "baseline_priced": baseline_priced,
        "actual_cost": round(actual_cost, 6),
        "baseline_cost": round(baseline_cost, 6),
        "savings": round(savings, 6),
        "savings_pct": round(savings / baseline_cost * 100, 2) if baseline_cost > 0 else 0.0,
        "requests": sum(int(row["requests"]) for row in totals["included"]),
        "excluded": {
            "subscription_requests": int(totals["subscription"]["requests"]),
            "unpriced_requests": int(totals["unpriced"]["requests"]),
        },
        "by_model": by_model[:BY_MODEL_LIMIT],
    }


async def _resolve_baseline(db_path: str | Path, baseline: str | None) -> str:
    if baseline:
        return baseline
    settings = await get_all_settings(db_path)
    return resolve_savings_baseline(settings)


async def savings_for_days(
    db_path: str | Path,
    *,
    baseline: str | None,
    registry: PricingRegistry,
    days: int,
) -> dict[str, Any]:
    resolved = await _resolve_baseline(db_path, baseline)
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    end = now.isoformat(sep=" ", timespec="seconds")
    totals = await get_savings_window_totals(db_path, start=start, end=end)
    payload = build_savings(totals, baseline=resolved, registry=registry)
    payload["window"] = {"kind": "days", "days": days}
    return payload


async def savings_for_today(
    db_path: str | Path,
    *,
    baseline: str | None,
    registry: PricingRegistry,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = await _resolve_baseline(db_path, baseline)
    window = await current_reporting_day(db_path, now=now)
    start, end = window.query_bounds
    totals = await get_savings_window_totals(db_path, start=start, end=end)
    payload = build_savings(totals, baseline=resolved, registry=registry)
    payload.pop("by_model")
    payload["window"] = {"kind": "today", "reporting_timezone": window.timezone}
    return payload
