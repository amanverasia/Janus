from __future__ import annotations

import copy
from typing import Any, Literal

from janus.catalog import inventory_entries

BillingModel = Literal["prepaid", "postpaid", "free_tier", "unknown"]

INVENTORY_PROVIDERS: dict[str, dict[str, Any]] = inventory_entries()


def get_inventory_providers() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(INVENTORY_PROVIDERS)


def get_inventory_provider(provider_id: str) -> dict[str, Any] | None:
    provider = INVENTORY_PROVIDERS.get(provider_id)
    if provider is None:
        return None
    return copy.deepcopy(provider)
