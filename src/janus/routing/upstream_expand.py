from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from janus.config.schema import ProviderConfig


def _region_aligned_transports(
    transports: dict[str, str] | None,
    base_url: str,
    default_base_url: str,
) -> dict[str, str] | None:
    """Rewrite multi-format transport hosts when a key uses a regional base URL.

    Token-plan keys are cluster-specific (sgp/cn/ams). If inventory sets
    custom_base_url to another region, Anthropic/OpenAI alternate transports
    must follow that host rather than the gateway default (sgp).
    """
    if not transports:
        return transports
    key_host = urlparse(base_url).netloc
    default_host = urlparse(default_base_url).netloc
    if not key_host or key_host == default_host:
        return transports
    rewritten: dict[str, str] = {}
    for fmt, url in transports.items():
        parsed = urlparse(url)
        if parsed.netloc and parsed.netloc == default_host:
            rewritten[fmt] = parsed._replace(netloc=key_host).geturl()
        else:
            rewritten[fmt] = url
    return rewritten


def expand_gateway_provider(
    row: dict[str, Any],
    upstream_keys: list[dict[str, Any]],
) -> list[ProviderConfig]:
    models = json.loads(row["models"]) if row["models"] else []
    allowed_models = json.loads(row["allowed_models"]) if row.get("allowed_models") else []
    quota_window = row.get("quota_window") or None
    quota_limit = row.get("quota_limit")
    quota_metric = row.get("quota_metric") or "requests"
    transports_raw = row.get("transports")
    transports: dict[str, str] | None = None
    if isinstance(transports_raw, str) and transports_raw:
        try:
            transports_raw = json.loads(transports_raw)
        except (json.JSONDecodeError, TypeError):
            transports_raw = None
    if isinstance(transports_raw, dict):
        transports = {str(k): str(v) for k, v in transports_raw.items() if v}
    elif isinstance(transports_raw, list):
        # Accept list[{format, base_url}] from older seed/UI shapes.
        parsed: dict[str, str] = {}
        for item in transports_raw:
            if not isinstance(item, dict):
                continue
            fmt = item.get("format")
            url = item.get("base_url") or item.get("url")
            if fmt and url:
                parsed[str(fmt)] = str(url)
        transports = parsed or None
    if upstream_keys:
        configs: list[ProviderConfig] = []
        for key in upstream_keys:
            base_url = key.get("custom_base_url") or row["base_url"]
            configs.append(
                ProviderConfig(
                    id=f"{row['id']}::uk_{key['id']}",
                    prefix=row["prefix"],
                    api_type=row["api_type"],
                    base_url=base_url,
                    api_key=key["key_value"],
                    models=models,
                    allowed_models=allowed_models,
                    upstream_key_id=key["id"],
                    rate_limit_rpm=key.get("rate_limit_rpm"),
                    rate_limit_rpd=key.get("rate_limit_rpd"),
                    quota_window=quota_window,
                    quota_limit=quota_limit,
                    quota_metric=quota_metric,
                    transports=_region_aligned_transports(transports, base_url, row["base_url"]),
                )
            )
        return configs
    return [
        ProviderConfig(
            id=row["id"],
            prefix=row["prefix"],
            api_type=row["api_type"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            models=models,
            allowed_models=allowed_models,
            quota_window=quota_window,
            quota_limit=quota_limit,
            quota_metric=quota_metric,
            transports=transports,
        )
    ]
