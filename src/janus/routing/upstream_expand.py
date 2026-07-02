from __future__ import annotations

import json
from typing import Any

from janus.config.schema import ProviderConfig


def expand_gateway_provider(
    row: dict[str, Any],
    upstream_keys: list[dict[str, Any]],
) -> list[ProviderConfig]:
    models = json.loads(row["models"]) if row["models"] else []
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
        )
    ]
