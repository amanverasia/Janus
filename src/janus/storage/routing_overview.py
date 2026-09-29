from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
from janus.storage.cooldowns import get_active_cooldowns
from janus.storage.providers_db import list_providers
from janus.storage.quotas import describe_reset, get_window_usages, quota_status
from janus.storage.upstream_keys import list_routable_upstream_keys_for_providers


def _account_cooldown(
    cooldowns: dict[str, tuple[float, int]], account_id: str, now: float
) -> tuple[bool, float]:
    prefix = f"{account_id}::"
    max_expiry: float | None = None
    for combined, (expires_at, _level) in cooldowns.items():
        if combined.startswith(prefix) and expires_at > now:
            if max_expiry is None or expires_at > max_expiry:
                max_expiry = expires_at
    if max_expiry is None:
        return False, 0.0
    return True, max(0.0, max_expiry - now)


async def get_routing_overview(db_path: str | Path) -> dict[str, Any]:
    now = time.time()
    cooldowns = await get_active_cooldowns(db_path)
    provider_rows = await list_providers(db_path, enabled_only=True)
    inventory_ids = list(
        dict.fromkeys(inventory_provider_id_for_prefix(row["prefix"]) for row in provider_rows)
    )
    routable_by_provider = await list_routable_upstream_keys_for_providers(db_path, inventory_ids)
    quota_providers = [
        (str(row["id"]), str(row["quota_window"]))
        for row in provider_rows
        if row.get("quota_window") and row.get("quota_limit")
    ]
    quota_usages = await get_window_usages(db_path, quota_providers)

    providers: list[dict[str, Any]] = []
    for row in provider_rows:
        inventory_id = inventory_provider_id_for_prefix(row["prefix"])
        routable = routable_by_provider[inventory_id]

        quota: dict[str, Any] | None = None
        if row.get("quota_window") and row.get("quota_limit"):
            usage = quota_usages[str(row["id"])]
            metric = row.get("quota_metric") or "requests"
            used = usage["tokens"] if metric == "tokens" else usage["requests"]
            limit = int(row["quota_limit"])
            status = quota_status(used, limit)
            quota = {
                "window": row["quota_window"],
                "used": used,
                "limit": limit,
                "metric": metric,
                "status": status,
                "percent": min(round(used * 100 / limit), 100) if limit else 0,
                "exhausted": status == "exhausted",
                **describe_reset(str(row["quota_window"])),
            }
        quota_exhausted = quota is not None and quota["exhausted"]

        accounts: list[dict[str, Any]] = []
        if routable:
            for index, key in enumerate(routable, start=1):
                account_id = str(key["id"])
                cooldown_active, cooldown_seconds = _account_cooldown(cooldowns, account_id, now)
                accounts.append(
                    {
                        "order": index,
                        "account_id": account_id,
                        "config_id": f"{row['id']}::uk_{key['id']}",
                        "key_id": key["id"],
                        "key_masked": key.get("key_masked", "—"),
                        "key_label": key.get("key_label"),
                        "priority": int(key.get("priority") or 0),
                        "credits_remaining": key.get("credits_remaining"),
                        "source": "inventory",
                        "cooldown_active": cooldown_active,
                        "cooldown_seconds": cooldown_seconds,
                        "quota_deprioritized": quota_exhausted,
                    }
                )
        elif row.get("api_key"):
            account_id = str(row["id"])
            cooldown_active, cooldown_seconds = _account_cooldown(cooldowns, account_id, now)
            accounts.append(
                {
                    "order": 1,
                    "account_id": account_id,
                    "config_id": row["id"],
                    "key_id": None,
                    "key_masked": "provider config",
                    "key_label": None,
                    "priority": 0,
                    "credits_remaining": None,
                    "source": "config",
                    "cooldown_active": cooldown_active,
                    "cooldown_seconds": cooldown_seconds,
                    "quota_deprioritized": quota_exhausted,
                }
            )

        providers.append(
            {
                "id": row["id"],
                "prefix": row["prefix"],
                "inventory_provider_id": inventory_id,
                "account_count": len(accounts),
                "accounts": accounts,
                "quota": quota,
            }
        )

    cooled_accounts = {
        combined.rpartition("::")[0]
        for combined, (expires_at, _level) in cooldowns.items()
        if expires_at > now
    }
    cooled_count = len(cooled_accounts)

    return {
        "providers": providers,
        "cooldown_count": cooled_count,
    }
