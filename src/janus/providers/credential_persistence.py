from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .oauth_tokens import parse_credential, refresh_token, serialize_credential

logger = logging.getLogger(__name__)

_PENDING_SAVES: set[asyncio.Task[None]] = set()


async def drain_pending_credential_saves(timeout: float = 5.0) -> None:
    if _PENDING_SAVES:
        await asyncio.wait(set(_PENDING_SAVES), timeout=timeout)


class CredentialStore(Protocol):
    async def load(self) -> str | None: ...

    async def save(self, previous: str, current: str) -> bool: ...


def credential_expiry(cred: dict[str, Any]) -> float | None:
    raw = cred.get("expires_at") or cred.get("expiresAt")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value / 1000.0 if value > 1e12 else value


class PersistentCredentialMixin:
    _cred: dict[str, Any]
    _credential_store: CredentialStore | None
    _stored_blob: str

    def _init_credential_persistence(self, api_key: str) -> None:
        self._credential_store = None
        self._stored_blob = api_key

    def attach_credential_store(self, store: CredentialStore) -> None:
        self._credential_store = store

    async def _load_stored_credential(self) -> tuple[str, dict[str, Any]] | None:
        store = self._credential_store
        if store is None:
            return None
        try:
            stored = await store.load()
        except Exception:
            logger.warning("Could not read stored OAuth credential for %s", type(self).__name__)
            return None
        if not stored or stored == self._stored_blob:
            return None
        return stored, parse_credential(stored)

    async def _adopt_newer_stored_credential(self) -> bool:
        loaded = await self._load_stored_credential()
        if loaded is None:
            return False
        stored, cred = loaded
        stored_expiry = credential_expiry(cred)
        current_expiry = credential_expiry(self._cred)
        if stored_expiry is None or (
            current_expiry is not None and stored_expiry <= current_expiry
        ):
            return False
        self._cred = cred
        self._stored_blob = stored
        return True

    async def _adopt_rotated_stored_credential(self) -> bool:
        loaded = await self._load_stored_credential()
        if loaded is None:
            return False
        stored, cred = loaded
        stored_refresh = refresh_token(cred)
        if not stored_refresh or stored_refresh == refresh_token(self._cred):
            return False
        self._cred = cred
        self._stored_blob = stored
        return True

    def _persist_credential(self) -> None:
        store = self._credential_store
        if store is None:
            return
        current = serialize_credential(self._cred)
        previous = self._stored_blob
        if current == previous:
            return
        task = asyncio.create_task(self._save_credential(store, previous, current))
        _PENDING_SAVES.add(task)
        task.add_done_callback(_PENDING_SAVES.discard)

    async def _save_credential(self, store: CredentialStore, previous: str, current: str) -> None:
        try:
            saved = await store.save(previous, current)
        except Exception:
            logger.warning(
                "Could not persist refreshed OAuth credential for %s", type(self).__name__
            )
            return
        if saved and self._stored_blob == previous:
            self._stored_blob = current
