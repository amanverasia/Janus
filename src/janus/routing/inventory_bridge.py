from __future__ import annotations

PREFIX_TO_INVENTORY: dict[str, str] = {
    "gemini": "google",
}


def inventory_provider_id_for_prefix(prefix: str) -> str:
    return PREFIX_TO_INVENTORY.get(prefix, prefix)
