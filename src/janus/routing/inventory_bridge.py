from __future__ import annotations

from janus.catalog import prefix_to_inventory_map

PREFIX_TO_INVENTORY: dict[str, str] = prefix_to_inventory_map()


def inventory_provider_id_for_prefix(prefix: str) -> str:
    return PREFIX_TO_INVENTORY.get(prefix, prefix)
