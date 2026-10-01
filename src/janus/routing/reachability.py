from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from janus.catalog import PROVIDERS
from janus.models.catalog import _string_list
from janus.providers.registry import PREFIX_ALIASES, ProviderRegistry
from janus.routing.fallback import FallbackHandler


class UnreachableReason(StrEnum):
    NO_PROVIDER = "no_provider"
    PROVIDER_DISABLED = "provider_disabled"
    NO_ACTIVE_CREDENTIAL = "no_active_credential"
    MODEL_NOT_ENABLED = "model_not_enabled"


@dataclass(frozen=True)
class KnownModel:
    model: str
    prefix: str
    catalog_id: str
    source: str


@dataclass(frozen=True)
class UnreachableModel:
    model: str
    prefix: str
    catalog_id: str
    reason: UnreachableReason
    source: str


@dataclass(frozen=True)
class ReachabilityReport:
    unreachable: list[UnreachableModel]
    reachable: list[KnownModel]


def _gateway(catalog_id: str) -> dict[str, Any] | None:
    entry = PROVIDERS.get(catalog_id)
    if not isinstance(entry, dict):
        return None
    gateway = entry.get("gateway")
    return gateway if isinstance(gateway, dict) else None


def _gateway_prefix(catalog_id: str) -> str:
    gateway = _gateway(catalog_id)
    if gateway is None:
        return ""
    prefix = gateway.get("prefix")
    return prefix if isinstance(prefix, str) else ""


def _catalog_id_for_prefix(prefix: str) -> str:
    for catalog_id in PROVIDERS:
        if _gateway_prefix(catalog_id) == prefix:
            return catalog_id
    return prefix


def _bare_model(prefix: str, model: str) -> str:
    namespaced = f"{prefix}/"
    if model.startswith(namespaced):
        return model[len(namespaced) :]
    return model


def _canonical_prefix(prefix: str) -> str:
    return PREFIX_ALIASES.get(prefix, prefix)


def _truthy(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def collect_known_models(
    provider_rows: Sequence[Mapping[str, Any]],
    discovered_rows: Sequence[Mapping[str, Any]],
) -> list[KnownModel]:
    known: dict[tuple[str, str], KnownModel] = {}

    def add(prefix: str, model: str, catalog_id: str, source: str) -> None:
        if not prefix or not model:
            return
        bare = _bare_model(prefix, model)
        if not bare:
            return
        key = (prefix, bare)
        if key not in known:
            known[key] = KnownModel(model=bare, prefix=prefix, catalog_id=catalog_id, source=source)

    for catalog_id in PROVIDERS:
        gateway = _gateway(catalog_id)
        prefix = _gateway_prefix(catalog_id)
        if gateway is None or not prefix:
            continue
        defaults = gateway.get("default_models", [])
        if not isinstance(defaults, list):
            continue
        for model in defaults:
            if isinstance(model, str):
                add(prefix, model, catalog_id, "catalog")

    for row in provider_rows:
        row_prefix = row.get("prefix")
        if not isinstance(row_prefix, str) or not row_prefix:
            continue
        row_catalog_id = row.get("catalog_id")
        catalog_id = (
            row_catalog_id
            if isinstance(row_catalog_id, str) and row_catalog_id
            else _catalog_id_for_prefix(row_prefix)
        )
        for model in _string_list(row.get("models")):
            add(row_prefix, model, catalog_id, "configured")

    for row in discovered_rows:
        provider_id = row.get("provider_id")
        model_id = row.get("model_id")
        if not isinstance(provider_id, str) or not isinstance(model_id, str):
            continue
        discovered_prefix = _gateway_prefix(provider_id)
        if not discovered_prefix:
            continue
        add(discovered_prefix, model_id, provider_id, "discovered")

    return [known[key] for key in sorted(known)]


def classify(
    known: Sequence[KnownModel],
    registry: ProviderRegistry,
    provider_rows: Sequence[Mapping[str, Any]],
) -> ReachabilityReport:
    rows_by_prefix: dict[str, list[Mapping[str, Any]]] = {}
    for row in provider_rows:
        prefix = row.get("prefix")
        if isinstance(prefix, str) and prefix:
            rows_by_prefix.setdefault(_canonical_prefix(prefix), []).append(row)

    unreachable: list[UnreachableModel] = []
    reachable: list[KnownModel] = []
    for item in known:
        if registry.has_route(f"{item.prefix}/{item.model}"):
            reachable.append(item)
            continue
        canonical = _canonical_prefix(item.prefix)
        rows = rows_by_prefix.get(canonical, [])
        enabled = [row for row in rows if _truthy(row.get("is_enabled", 1))]
        if not rows:
            reason = UnreachableReason.NO_PROVIDER
        elif not enabled:
            reason = UnreachableReason.PROVIDER_DISABLED
        elif not registry.providers.get(canonical):
            reason = UnreachableReason.NO_ACTIVE_CREDENTIAL
        else:
            reason = UnreachableReason.MODEL_NOT_ENABLED
        unreachable.append(
            UnreachableModel(
                model=item.model,
                prefix=item.prefix,
                catalog_id=item.catalog_id,
                reason=reason,
                source=item.source,
            )
        )
    return ReachabilityReport(unreachable=unreachable, reachable=reachable)


def cooled_down(
    reachable: Sequence[KnownModel],
    registry: ProviderRegistry,
    handler: FallbackHandler,
    *,
    limit: int = 100,
) -> list[KnownModel]:
    result: list[KnownModel] = []
    if limit <= 0:
        return result
    for item in reachable:
        configs = registry.providers.get(_canonical_prefix(item.prefix), [])
        if not configs:
            continue
        if all(
            not handler.is_available(config.upstream_key_id or config.id, item.model)
            for config in configs
        ):
            result.append(item)
            if len(result) >= limit:
                break
    return result


def connect_target(catalog_id: str) -> dict[str, str]:
    entry = PROVIDERS.get(catalog_id)
    if isinstance(entry, dict) and isinstance(entry.get("inventory"), dict):
        return {
            "kind": "connect",
            "href": f"/dashboard/ui/connect?provider={quote(catalog_id, safe='')}",
        }
    return {"kind": "providers", "href": "/dashboard/ui/providers"}
