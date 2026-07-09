from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
from janus.storage.combos_db import list_combos
from janus.storage.cooldowns import get_active_cooldowns
from janus.storage.providers_db import list_providers
from janus.storage.upstream_keys import list_routable_upstream_keys


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

    providers: list[dict[str, Any]] = []
    for row in provider_rows:
        inventory_id = inventory_provider_id_for_prefix(row["prefix"])
        routable = await list_routable_upstream_keys(db_path, inventory_id)
        models = json.loads(row["models"]) if row["models"] else []

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
                }
            )

        providers.append(
            {
                "id": row["id"],
                "prefix": row["prefix"],
                "inventory_provider_id": inventory_id,
                "models": models,
                "account_count": len(accounts),
                "accounts": accounts,
            }
        )

    combos: list[dict[str, Any]] = []
    for combo_row in await list_combos(db_path):
        models = json.loads(combo_row["models"]) if combo_row["models"] else []
        combos.append({"name": combo_row["name"], "models": models})

    cooled_accounts = {
        combined.rpartition("::")[0]
        for combined, (expires_at, _level) in cooldowns.items()
        if expires_at > now
    }
    cooled_count = len(cooled_accounts)

    return {
        "providers": providers,
        "combos": combos,
        "cooldown_count": cooled_count,
        "rotation_note": (
            "Within each provider prefix, Janus tries accounts in the order shown "
            "(priority DESC, then credits). Account strategy (fill-first / round-robin / "
            "sticky round-robin) controls rotation. Sticky client-key routing only pins a "
            "Janus API key to one upstream account under fill-first; with round-robin it "
            "staggers each client's start offset but still rotates the multi-key pool. "
            "On 429/5xx/auth errors, the account is cooled down and the next is tried."
        ),
    }
