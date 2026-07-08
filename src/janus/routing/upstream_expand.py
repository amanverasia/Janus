from __future__ import annotations

import json
from typing import Any

from janus.config.schema import ProviderConfig


def expand_gateway_provider(
    row: dict[str, Any],
    upstream_keys: list[dict[str, Any]],
) -> list[ProviderConfig]:
    models = json.loads(row["models"]) if row["models"] else []
    quota_window = row.get("quota_window") or None
    quota_limit = row.get("quota_limit")
    quota_metric = row.get("quota_metric") or "requests"
    transports_raw = row.get("transports")
    transports: dict[str, str] | None = None
    if isinstance(transports_raw, str) and transports_raw:
        try:
            transports = json.loads(transports_raw)
        except (json.JSONDecodeError, TypeError):
            transports = None
    elif isinstance(transports_raw, dict):
        transports = transports_raw
    if upstream_keys:
        return [
            ProviderConfig(
                id=f"{row['id']}::uk_{key['id']}",
                prefix=row["prefix"],
                api_type=row["api_type"],
                base_url=key.get("custom_base_url") or row["base_url"],
                api_key=key["key_value"],
                models=models,
                upstream_key_id=key["id"],
                rate_limit_rpm=key.get("rate_limit_rpm"),
                rate_limit_rpd=key.get("rate_limit_rpd"),
                quota_window=quota_window,
                quota_limit=quota_limit,
                quota_metric=quota_metric,
                transports=transports,
            )
            for key in upstream_keys
        ]
    return [
        ProviderConfig(
            id=row["id"],
            prefix=row["prefix"],
            api_type=row["api_type"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            models=models,
            quota_window=quota_window,
            quota_limit=quota_limit,
            quota_metric=quota_metric,
            transports=transports,
        )
    ]
