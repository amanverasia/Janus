from __future__ import annotations

from typing import Any

TOKENPLAN_REGIONS: dict[str, str] = {
    "sgp": "https://token-plan-sgp.xiaomimimo.com/v1",
    "cn": "https://token-plan-cn.xiaomimimo.com/v1",
    "ams": "https://token-plan-ams.xiaomimimo.com/v1",
}

DEFAULT_TOKENPLAN_REGION = "sgp"
TOKENPLAN_PROVIDER_ID = "xiaomi_tokenplan"
XIAOMI_PAYGO_PROVIDER_ID = "xiaomi"


def tokenplan_region_base_urls() -> list[tuple[str, str]]:
    """Stable try-order: Singapore first (9router default), then CN, then AMS."""
    order = ("sgp", "cn", "ams")
    return [(region, TOKENPLAN_REGIONS[region]) for region in order]


def region_id_for_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    normalized = base_url.rstrip("/")
    for region, url in TOKENPLAN_REGIONS.items():
        if normalized == url.rstrip("/") or normalized.startswith(url.rstrip("/")):
            return region
    return None


def anthropic_transport_for_base(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1") + "/anthropic/v1"


def metadata_with_region(
    metadata: dict[str, Any] | None,
    *,
    region: str,
    base_url: str,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged["custom_base_url"] = base_url.rstrip("/")
    merged["tokenplan_region"] = region
    return merged
