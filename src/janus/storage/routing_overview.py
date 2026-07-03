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
                expires_at = cooldowns.get(account_id)
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
                        "cooldown_active": expires_at is not None and expires_at > now,
                        "cooldown_seconds": max(0.0, expires_at - now) if expires_at else 0.0,
                    }
                )
        elif row.get("api_key"):
            account_id = str(row["id"])
            expires_at = cooldowns.get(account_id)
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
                    "cooldown_active": expires_at is not None and expires_at > now,
                    "cooldown_seconds": max(0.0, expires_at - now) if expires_at else 0.0,
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

    cooled_count = sum(1 for expires_at in cooldowns.values() if expires_at > now)

    return {
        "providers": providers,
        "combos": combos,
        "cooldown_count": cooled_count,
        "rotation_note": (
            "Within each provider prefix, Janus tries accounts in the order shown "
            "(priority DESC, then credits). Available accounts rotate round-robin per request. "
            "On 429/5xx/auth errors, the account is cooled down and the next is tried."
        ),
    }
