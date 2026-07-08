from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .database import get_connection

Dimension = Literal["model", "provider", "account", "client_key"]

_DIMENSION_COLUMN = {
    "model": "model",
    "provider": "provider_id",
    "account": "account_id",
    "client_key": "client_key_id",
}


async def get_spend_summary(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT COUNT(*) as cnt,
                      COALESCE(SUM(input_tokens), 0) as inp,
                      COALESCE(SUM(output_tokens), 0) as outp,
                      COALESCE(SUM(cache_creation_tokens), 0) as cc,
                      COALESCE(SUM(cache_read_tokens), 0) as cr,
                      COALESCE(SUM(cost), 0.0) as cost
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None

        async with db.execute(
            """SELECT date(timestamp) as date,
                      COUNT(*) as requests,
                      COALESCE(SUM(cost), 0.0) as cost,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(input_tokens), 0)
                        + COALESCE(SUM(output_tokens), 0) as tokens
               FROM usage
               WHERE timestamp >= datetime('now', ?)
               GROUP BY date(timestamp)
               ORDER BY date(timestamp)""",
            (f"-{days} days",),
        ) as cur:
            daily_rows = await cur.fetchall()

    return {
        "total_cost": row["cost"],
        "total_requests": row["cnt"],
        "total_input_tokens": row["inp"],
        "total_output_tokens": row["outp"],
        "total_cache_creation_tokens": row["cc"],
        "total_cache_read_tokens": row["cr"],
        "daily": [dict(r) for r in daily_rows],
    }


async def get_flow(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                   COALESCE(k.name, u.client_key_label, 'Direct (no API key)') as source,
                   COALESCE(u.model, 'unknown') as model,
                   CASE
                     WHEN u.provider_id LIKE '%::%'
                     THEN substr(u.provider_id, 1, instr(u.provider_id, '::') - 1)
                     ELSE COALESCE(u.provider_id, 'unknown')
                   END as provider,
                   COUNT(*) as requests,
                   COALESCE(SUM(u.input_tokens), 0)
                     + COALESCE(SUM(u.output_tokens), 0) as tokens,
                   COALESCE(SUM(u.cost), 0.0) as cost
               FROM usage u
               LEFT JOIN api_keys k ON u.client_key_id = k.id
               WHERE u.timestamp >= datetime('now', ?)
               GROUP BY source, model, provider""",
            (f"-{days} days",),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    nodes: list[dict[str, str]] = []
    node_index: dict[str, int] = {}

    def _node(name: str, kind: str) -> int:
        node_id = f"{kind}:{name}"
        if node_id not in node_index:
            node_index[node_id] = len(nodes)
            nodes.append({"name": name, "kind": kind})
        return node_index[node_id]

    links: dict[tuple[int, int], dict[str, float]] = {}

    def _link(src: int, dst: int, requests: int, tokens: int, cost: float) -> None:
        key = (src, dst)
        agg = links.setdefault(key, {"requests": 0, "tokens": 0, "cost": 0.0})
        agg["requests"] += requests
        agg["tokens"] += tokens
        agg["cost"] += cost

    for r in rows:
        key_node = _node(str(r["source"]), "key")
        model_node = _node(str(r["model"]), "model")
        provider_node = _node(str(r["provider"]), "provider")
        _link(key_node, model_node, r["requests"], r["tokens"], r["cost"])
        _link(model_node, provider_node, r["requests"], r["tokens"], r["cost"])

    link_list = [
        {
            "source": src,
            "target": dst,
            "requests": vals["requests"],
            "tokens": vals["tokens"],
            "cost": round(vals["cost"], 6),
        }
        for (src, dst), vals in links.items()
    ]
    return {"nodes": nodes, "links": link_list}


async def get_breakdown(
    db_path: str | Path, *, dimension: Dimension, days: int = 30
) -> list[dict[str, Any]]:
    if dimension == "client_key":
        async with get_connection(db_path) as db:
            async with db.execute(
                """SELECT
                       COALESCE(k.name, u.client_key_label, 'Direct (no API key)') as client_key,
                       COUNT(*) as requests,
                       COALESCE(SUM(u.input_tokens), 0) as input_tokens,
                       COALESCE(SUM(u.output_tokens), 0) as output_tokens,
                       COALESCE(SUM(u.cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(u.cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(u.cost), 0.0) as cost
                FROM usage u
                LEFT JOIN api_keys k ON u.client_key_id = k.id
                WHERE u.timestamp >= datetime('now', ?)
                GROUP BY COALESCE(k.name, u.client_key_label, 'Direct (no API key)')
                ORDER BY cost DESC""",
                (f"-{days} days",),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    if dimension == "provider":
        async with get_connection(db_path) as db:
            async with db.execute(
                """SELECT
                       CASE
                         WHEN provider_id LIKE '%::%'
                         THEN substr(provider_id, 1, instr(provider_id, '::') - 1)
                         ELSE COALESCE(provider_id, 'unknown')
                       END as provider,
                       COUNT(*) as requests,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(cost), 0.0) as cost
                FROM usage
                WHERE timestamp >= datetime('now', ?)
                GROUP BY provider
                ORDER BY cost DESC""",
                (f"-{days} days",),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    if dimension == "account":
        async with get_connection(db_path) as db:
            async with db.execute(
                """SELECT
                       COALESCE(
                         NULLIF(uk.key_label, ''),
                         uk.key_masked,
                         u.account_id,
                         'unknown'
                       ) as account,
                       COUNT(*) as requests,
                       COALESCE(SUM(u.input_tokens), 0) as input_tokens,
                       COALESCE(SUM(u.output_tokens), 0) as output_tokens,
                       COALESCE(SUM(u.cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(u.cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(u.cost), 0.0) as cost
                FROM usage u
                LEFT JOIN upstream_keys uk ON u.account_id = uk.id
                WHERE u.timestamp >= datetime('now', ?)
                GROUP BY COALESCE(u.account_id, 'unknown')
                ORDER BY cost DESC""",
                (f"-{days} days",),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    col = _DIMENSION_COLUMN[dimension]
    async with get_connection(db_path) as db:
        async with db.execute(
            f"""SELECT {col} as {dimension},
                       COUNT(*) as requests,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(cost), 0.0) as cost
                FROM usage
                WHERE timestamp >= datetime('now', ?)
                GROUP BY {col}
                ORDER BY cost DESC""",
            (f"-{days} days",),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_success_rate(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                SUM(CASE WHEN status >= 200 AND status < 300 THEN 1 ELSE 0 END) as s2xx,
                SUM(CASE WHEN status >= 400 AND status < 500 THEN 1 ELSE 0 END) as s4xx,
                SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) as s5xx,
                COUNT(*) as total
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
    return {
        "success_2xx": row["s2xx"] or 0,
        "client_4xx": row["s4xx"] or 0,
        "server_5xx": row["s5xx"] or 0,
        "total": row["total"] or 0,
    }


async def get_leaderboard(
    db_path: str | Path,
    *,
    days: int = 30,
    sort_by: str = "tokens",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Leaderboard of API keys ranked by usage (tokens, cost, or requests).

    Includes ALL active keys — even those with zero usage — so everyone
    appears on the board. Keys with usage are ranked first by the sort metric;
    keys with zero usage are appended alphabetically at the end.
    """
    sort_col = {"tokens": "tokens", "cost": "cost", "requests": "requests"}.get(sort_by, "tokens")
    if days <= 0:
        time_clause = "1=1"
        time_params: tuple[str, ...] = ()
    else:
        time_clause = "u.timestamp >= datetime('now', ?)"
        time_params = (f"-{days} days",)
    async with get_connection(db_path) as db:
        # Keys with usage in the time window.
        async with db.execute(
            f"""SELECT
                   COALESCE(k.name, u.client_key_label, 'Direct (no API key)') as key_name,
                   MAX(k.id) as key_id,
                   COUNT(*) as requests,
                   COALESCE(SUM(u.input_tokens), 0)
                     + COALESCE(SUM(u.output_tokens), 0) as tokens,
                   COALESCE(SUM(u.input_tokens), 0) as input_tokens,
                   COALESCE(SUM(u.output_tokens), 0) as output_tokens,
                   COALESCE(SUM(u.cost), 0.0) as cost,
                   CASE WHEN COUNT(*) > 0
                     THEN CAST(
                       SUM(CASE WHEN u.status >= 200 AND u.status < 300 THEN 1 ELSE 0 END)
                       AS REAL) / COUNT(*) * 100
                     ELSE 0.0
                   END as success_pct
            FROM usage u
            LEFT JOIN api_keys k ON u.client_key_id = k.id
            WHERE {time_clause}
            GROUP BY COALESCE(k.name, u.client_key_label, 'Direct (no API key)')
            ORDER BY {sort_col} DESC
            LIMIT ?""",
            time_params + (limit,),
        ) as cur:
            used_rows = await cur.fetchall()

        # Active keys with zero usage in this window — append at the end.
        used_names = {str(row["key_name"]) for row in used_rows}
        async with db.execute(
            "SELECT name as key_name, id as key_id FROM api_keys WHERE is_active = 1 ORDER BY name"
        ) as cur:
            all_keys = await cur.fetchall()
        zero_keys = [
            {"key_name": r["key_name"], "key_id": r["key_id"]}
            for r in all_keys
            if str(r["key_name"]) not in used_names
        ]

    result: list[dict[str, Any]] = []
    for i, row in enumerate(used_rows):
        result.append(
            {
                "rank": i + 1,
                "key_name": row["key_name"],
                "requests": row["requests"],
                "tokens": row["tokens"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cost": round(row["cost"], 6),
                "success_pct": round(row["success_pct"], 1),
            }
        )
    for zk in zero_keys:
        result.append(
            {
                "rank": len(result) + 1,
                "key_name": zk["key_name"],
                "requests": 0,
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "success_pct": 0.0,
            }
        )
    return result
