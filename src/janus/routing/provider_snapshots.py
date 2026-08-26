from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler


@dataclass(eq=False)
class ProviderSnapshot:
    providers: dict[str, Provider]
    registry: ProviderRegistry
    handler: FallbackHandler
    model_catalog: Sequence[Mapping[str, Any]] = field(default_factory=list)
    leases: int = 0
    retired: bool = False
    closed: bool = False
    providers_to_close: list[Provider] = field(default_factory=list)


def _unique_providers(providers: list[Provider]) -> list[Provider]:
    seen: set[int] = set()
    unique: list[Provider] = []
    for provider in providers:
        identity = id(provider)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(provider)
    return unique


def ensure_provider_snapshot(app: FastAPI) -> ProviderSnapshot:
    providers: dict[str, Provider] = app.state.providers
    registry: ProviderRegistry = app.state.registry
    handler: FallbackHandler = app.state.fallback_handler
    snapshot: ProviderSnapshot | None = getattr(app.state, "provider_snapshot", None)
    if (
        snapshot is None
        or snapshot.providers is not providers
        or snapshot.registry is not registry
        or snapshot.handler is not handler
    ):
        snapshot = ProviderSnapshot(
            providers=providers,
            registry=registry,
            handler=handler,
            model_catalog=getattr(app.state, "model_catalog", []),
        )
        app.state.provider_snapshot = snapshot
    if not hasattr(app.state, "retired_provider_snapshots"):
        app.state.retired_provider_snapshots = []
    if not hasattr(app.state, "provider_snapshot_drain_lock"):
        app.state.provider_snapshot_drain_lock = asyncio.Lock()
    return snapshot


def acquire_provider_snapshot(app: FastAPI) -> ProviderSnapshot:
    snapshot = ensure_provider_snapshot(app)
    snapshot.leases += 1
    return snapshot


async def _drain_retired_snapshots(app: FastAPI) -> None:
    lock: asyncio.Lock = app.state.provider_snapshot_drain_lock
    async with lock:
        current: ProviderSnapshot = app.state.provider_snapshot
        retired: list[ProviderSnapshot] = app.state.retired_provider_snapshots
        active = [snapshot for snapshot in [current, *retired] if snapshot.leases > 0]
        active_provider_ids = {
            id(provider) for snapshot in active for provider in snapshot.providers.values()
        }
        providers_to_close = _unique_providers(
            [
                provider
                for snapshot in retired
                for provider in snapshot.providers_to_close
                if id(provider) not in active_provider_ids
            ]
        )
        closing_ids = {id(provider) for provider in providers_to_close}
        for snapshot in retired:
            snapshot.providers_to_close = [
                provider
                for provider in snapshot.providers_to_close
                if id(provider) not in closing_ids
            ]
        await asyncio.gather(
            *(provider.close() for provider in providers_to_close),
            return_exceptions=True,
        )
        completed = [
            snapshot
            for snapshot in retired
            if snapshot.leases == 0 and not snapshot.providers_to_close
        ]
        for snapshot in completed:
            snapshot.closed = True
            retired.remove(snapshot)


async def release_provider_snapshot(app: FastAPI, snapshot: ProviderSnapshot) -> None:
    if snapshot.leases <= 0:
        raise RuntimeError("Provider snapshot lease released more than once")
    snapshot.leases -= 1
    await _drain_retired_snapshots(app)


async def install_provider_snapshot(
    app: FastAPI,
    snapshot: ProviderSnapshot,
    *,
    providers_to_close: list[Provider],
) -> None:
    old_snapshot = ensure_provider_snapshot(app)
    old_snapshot.retired = True
    old_snapshot.providers_to_close = _unique_providers(providers_to_close)
    retired: list[ProviderSnapshot] = app.state.retired_provider_snapshots
    retired.append(old_snapshot)
    app.state.provider_snapshot = snapshot
    app.state.providers = snapshot.providers
    app.state.registry = snapshot.registry
    app.state.fallback_handler = snapshot.handler
    app.state.model_catalog = snapshot.model_catalog
    await _drain_retired_snapshots(app)


async def close_provider_snapshots(app: FastAPI) -> None:
    current = ensure_provider_snapshot(app)
    lock: asyncio.Lock = app.state.provider_snapshot_drain_lock
    async with lock:
        retired: list[ProviderSnapshot] = list(app.state.retired_provider_snapshots)
        providers = list(current.providers.values())
        for snapshot in retired:
            providers.extend(snapshot.providers_to_close)
        await asyncio.gather(
            *(provider.close() for provider in _unique_providers(providers)),
            return_exceptions=True,
        )
        current.closed = True
        for snapshot in retired:
            snapshot.closed = True
        app.state.retired_provider_snapshots = []
