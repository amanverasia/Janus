from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .database import get_connection

if TYPE_CHECKING:
    from janus.pricing.registry import PricingRegistry

logger = logging.getLogger(__name__)


def _not_subscription_provider_clause(table: str = "usage") -> tuple[str, tuple[Any, ...]]:
    """SQL fragment excluding rows recorded against subscription/OAuth providers.

    Subscription providers (Copilot, Kiro, Claude OAuth, ...) have no per-token
    marginal cost, so their $0 rows are intentional -- they must not be
    backfilled with catalog pricing nor reported as "unpriced".

    ``usage.provider_id`` is ``target.provider_config.id``, which is either the
    providers-table row id or that id with an ``::uk_N`` inventory-key suffix,
    so both the exact id and the prefix before ``::`` are matched.
    """
    from janus.pricing.calculator import SUBSCRIPTION_API_TYPES

    api_types = sorted(SUBSCRIPTION_API_TYPES)
    placeholders = ", ".join("?" for _ in api_types)
    clause = (
        f"NOT EXISTS (SELECT 1 FROM providers p "
        f"WHERE p.api_type IN ({placeholders}) "
        f"AND ({table}.provider_id = p.id OR {table}.provider_id LIKE p.id || '::%'))"
    )
    return clause, tuple(api_types)


async def record_usage(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    account_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    status: int = 0,
    client_key_id: int | None = None,
    client_key_label: str | None = None,
    cost: float = 0.0,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (provider_id, model, account_id, input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, status, client_key_id,
                    client_key_label, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider_id,
                    model,
                    account_id,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    status,
                    client_key_id,
                    client_key_label,
                    cost,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)


async def get_request_counts_today(db_path: str | Path) -> dict[str, int]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT account_id, COUNT(*) FROM usage
               WHERE date(timestamp) = date('now') AND account_id IS NOT NULL
               GROUP BY account_id"""
        ) as cur:
            rows = await cur.fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


async def get_unpriced_models(db_path: str | Path, days: int = 30) -> list[dict[str, Any]]:
    """Models seen in usage within the last ``days`` days that have zero total cost
    but nonzero token volume -- candidates for a missing pricing entry.

    Returns request counts and token sums, ordered by total tokens descending.
    Callers should further filter out models that the *current* pricing
    registry actually resolves (via ``registry.get``), since a model can have
    old $0 usage rows from before a catalog sync even though it's priced now.

    Rows recorded against subscription/OAuth providers are excluded: their $0
    cost is intentional, not a missing pricing entry.
    """
    sub_clause, sub_params = _not_subscription_provider_clause()
    async with get_connection(db_path) as db:
        async with db.execute(
            f"""SELECT model,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens
               FROM usage
               WHERE timestamp >= datetime('now', ?)
                 AND model IS NOT NULL
                 AND {sub_clause}
               GROUP BY model
               HAVING COALESCE(SUM(cost), 0.0) = 0.0
                  AND (COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)) > 0
               ORDER BY (input_tokens + output_tokens) DESC""",
            (f"-{days} days", *sub_params),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_usage_stats(db_path: str | Path) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(input_tokens),0) as inp,"
            "COALESCE(SUM(output_tokens),0) as outp FROM usage"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
        total_requests = row["cnt"]
        total_input = row["inp"]
        total_output = row["outp"]

        async with db.execute(
            """SELECT model, COUNT(*) as requests,
                      COALESCE(SUM(input_tokens),0) as input_tokens,
                      COALESCE(SUM(output_tokens),0) as output_tokens
               FROM usage GROUP BY model ORDER BY requests DESC"""
        ) as cur:
            model_rows = await cur.fetchall()

    return {
        "total_requests": total_requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "by_model": [dict(r) for r in model_rows],
    }


async def get_today_total_cost(db_path: str | Path) -> float:
    """Sum of ``usage.cost`` for rows timestamped today (local time).

    Used to report how much a backfill moved today's measured spend, which
    is what budget enforcement reads from.
    """
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost), 0.0) as total FROM usage "
            "WHERE date(timestamp) = date('now', 'localtime')"
        ) as cur:
            row = await cur.fetchone()
    return float(row["total"]) if row is not None else 0.0


async def backfill_costs(
    db_path: str | Path,
    registry: PricingRegistry,
    *,
    days: int | None = None,
    dry_run: bool = False,
) -> tuple[int, float]:
    """Recompute and persist cost for historical rows that were recorded as
    $0 before pricing was known, but did carry token usage.

    Only rows matching ``(cost IS NULL OR cost = 0) AND (input_tokens > 0 OR
    output_tokens > 0 OR cache_creation_tokens > 0 OR cache_read_tokens > 0)``
    are considered. Each candidate row's cost is
    recomputed via ``compute_cost`` using its stored token counts against the
    given (already up to date) pricing registry. A row is only written back
    when the recomputed cost is strictly greater than zero -- if the model is
    still unpriced, backfilling it now would just write another $0 and there
    is nothing to gain by touching the row.

    ``days`` optionally restricts the candidate set to rows timestamped
    within the last ``days`` days. ``dry_run`` computes the same totals but
    rolls back instead of committing, so nothing is persisted.

    Rows recorded against subscription/OAuth providers are never candidates:
    their $0 cost is correct, and pricing them from the catalog would
    fabricate spend that never happened.

    Runs as a single transaction. Returns ``(rows_updated, total_cost_added)``.
    """
    from janus.canonical.models import Usage
    from janus.pricing.calculator import compute_cost

    sub_clause, sub_params = _not_subscription_provider_clause()
    query = (
        "SELECT id, model, input_tokens, output_tokens, "
        "cache_creation_tokens, cache_read_tokens FROM usage "
        "WHERE (cost IS NULL OR cost = 0) "
        "AND (input_tokens > 0 OR output_tokens > 0 "
        "OR cache_creation_tokens > 0 OR cache_read_tokens > 0) "
        f"AND {sub_clause}"
    )
    params: tuple[Any, ...] = sub_params
    if days is not None:
        query += " AND timestamp >= datetime('now', ?)"
        params = (*sub_params, f"-{days} days")

    async with get_connection(db_path) as db:
        async with db.execute(query, params) as cur:
            candidates = await cur.fetchall()

        rows_updated = 0
        total_cost_added = 0.0
        updates: list[tuple[float, int]] = []
        for row in candidates:
            model = row["model"]
            if not model:
                continue
            usage = Usage(
                input_tokens=row["input_tokens"] or 0,
                output_tokens=row["output_tokens"] or 0,
                cache_creation_input_tokens=row["cache_creation_tokens"] or 0,
                cache_read_input_tokens=row["cache_read_tokens"] or 0,
            )
            new_cost = compute_cost(usage, model, registry)
            if new_cost <= 0:
                continue
            rows_updated += 1
            total_cost_added += new_cost
            updates.append((new_cost, row["id"]))

        if dry_run:
            await db.rollback()
        else:
            if updates:
                await db.executemany("UPDATE usage SET cost = ? WHERE id = ?", updates)
            await db.commit()

    return rows_updated, total_cost_added
