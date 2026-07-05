from __future__ import annotations

import copy
from typing import Any

from janus.catalog import gateway_entries

CATALOG: dict[str, dict[str, Any]] = gateway_entries()


def get_catalog() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(CATALOG)


def provider_logo_url(filename: str) -> str:
    return f"/dashboard/static/logos/{filename}"


def get_provider_logo_map() -> dict[str, str]:
    logos: dict[str, str] = {}
    for key, entry in CATALOG.items():
        logo = entry.get("logo")
        if not logo:
            continue
        logos[key] = str(logo)
        prefix = entry.get("prefix")
        if prefix:
            logos[str(prefix)] = str(logo)
    return logos
