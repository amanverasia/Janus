# OAuth Credential Write-back + Claude OAuth Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live OAuth executors persist refreshed credentials to their inventory row, validators stop competing for refresh tokens, and Claude OAuth becomes an inventory provider with a `/api/oauth/usage` account-value probe.

**Architecture:** A duck-typed `CredentialStore` (protocol in `providers/`, SQLite implementation in `inventory/`) is attached by `reload_providers` to every provider built for an inventory key (`ProviderConfig.upstream_key_id`). A shared `PersistentCredentialMixin` gives the four OAuth executors read-through before refresh and fire-and-forget compare-and-swap write-back after refresh. Claude onboarding reuses the Codex/Kiro/Antigravity inventory pattern (catalog `inventory` block, normalizer, ingestion detection, validator, probe).

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, httpx, respx, pytest-asyncio (`asyncio_mode = "auto"`), Svelte dashboard.

**Spec:** `docs/superpowers/specs/2026-10-01-oauth-writeback-claude-probe-design.md`

## Global Constraints

- `formats/` and `providers/` never import each other; `providers/` must not import `janus.inventory` or `janus.storage` (the store is injected).
- `ruff` line length 100, rules E,F,I,N,W,UP; `mypy --strict`; no code comments.
- Never log or return decrypted credentials, tokens, emails or account ids (logs name the executor class / key id only).
- Probes never refresh; expired token or 401/403 → probe `unavailable`, key status untouched; 10-minute TTL and in-flight dedup are reused, not reimplemented.
- Write-back never changes status/validity/usability/account-value columns and never triggers `reload_providers`.
- Run tools as `.venv/bin/python -m <tool>`; in the worktree use the main checkout's venv with `PYTHONPATH=$PWD/src:$PWD`.
- State-payload changes regenerate contract fixtures/shape signatures (`JANUS_REGEN_CONTRACT_FIXTURES=1`) and keep `test_dashboard_state_size.py` green in the same PR.

## Review Focus

1. Stored blob formatted differently from `serialize_credential` output (e.g. pasted with spaces) → CAS must use the exact stored string, so write-back still succeeds after the first refresh (Task 2 test `test_writeback_uses_exact_stored_string`).
2. User edits the Providers-page key while a refresh is in flight → `providers.api_key` must not be overwritten (Task 1 test `test_swap_skips_user_edited_provider_key`).
3. Refresh fails because a validator already rotated the refresh token → executor adopts the stored credential instead of returning 401 (Task 2 test `test_rejected_refresh_adopts_rotated_stored_credential`).
4. Claude Code `expiresAt` in milliseconds → normalized to seconds so `needs_refresh` works (Task 4 test `test_normalize_claude_code_file`).
5. `sk-ant-api…` Anthropic API keys must still detect as `anthropic`, only `sk-ant-oat…` as `claude_oauth` (Task 4 test `test_detect_provider_from_key_claude_oauth`).

---

### Task 1: Compare-and-swap credential storage + `CredentialStore`

**Files:**
- Modify: `src/janus/storage/upstream_keys.py` (add `swap_upstream_key_value`)
- Create: `src/janus/inventory/credential_store.py`
- Test: `tests/unit/inventory/test_credential_store.py`

**Interfaces:**
- Produces: `async def swap_upstream_key_value(db_path: str | Path, key_id: str, *, previous: str, current: str) -> bool`
- Produces: `class SqliteCredentialStore: __init__(db_path: str | Path, upstream_key_id: str); async load() -> str | None; async save(previous: str, current: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from typing import Any

from janus.inventory.credential_store import SqliteCredentialStore
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider, get_provider
from janus.storage.upstream_keys import (
    create_upstream_key,
    get_upstream_key,
    swap_upstream_key_value,
    update_upstream_key,
)

OLD = '{"access_token": "at-old", "refresh_token": "rt-old"}'
NEW = '{"access_token":"at-new","refresh_token":"rt-new"}'


async def _db(tmp_path: Any) -> Any:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    return db_path


async def test_swap_replaces_value_when_previous_matches(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == NEW
    assert row["status"] == key["status"]


async def test_swap_refuses_when_previous_differs(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    assert not await swap_upstream_key_value(
        db_path, str(key["id"]), previous="something-else", current=NEW
    )
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == OLD


async def test_swap_updates_mirrored_provider_key(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    await create_provider(
        db_path,
        {"id": "cl", "prefix": "claude", "api_type": "claude_oauth",
         "base_url": "https://api.anthropic.com", "api_key": OLD, "models": ["m"]},
    )
    key = await create_upstream_key(
        db_path, provider_id="claude_oauth", key_value=OLD, source_node="gateway:cl"
    )
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)
    provider = await get_provider(db_path, "cl")
    assert provider is not None and provider["api_key"] == NEW


async def test_swap_skips_user_edited_provider_key(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    await create_provider(
        db_path,
        {"id": "cl", "prefix": "claude", "api_type": "claude_oauth",
         "base_url": "https://api.anthropic.com", "api_key": "user-edited", "models": ["m"]},
    )
    key = await create_upstream_key(
        db_path, provider_id="claude_oauth", key_value=OLD, source_node="gateway:cl"
    )
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)
    provider = await get_provider(db_path, "cl")
    assert provider is not None and provider["api_key"] == "user-edited"


async def test_store_load_and_save(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    store = SqliteCredentialStore(db_path, str(key["id"]))
    assert await store.load() == OLD
    assert await store.save(OLD, NEW)
    assert await store.load() == NEW


async def test_store_load_returns_none_for_revoked_or_missing(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    await update_upstream_key(db_path, str(key["id"]), {"status": "revoked"})
    assert await SqliteCredentialStore(db_path, str(key["id"])).load() is None
    assert await SqliteCredentialStore(db_path, "missing").load() is None


async def test_store_save_swallows_errors(tmp_path: Any) -> None:
    store = SqliteCredentialStore(tmp_path / "missing-dir" / "nope.db", "k")
    assert await store.save(OLD, NEW) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_credential_store.py -v`
Expected: FAIL with `ImportError` (`swap_upstream_key_value` / `credential_store` missing).

- [ ] **Step 3: Implement `swap_upstream_key_value`** (append to `storage/upstream_keys.py`; add `CredentialDecryptionError`, `decrypt_key_value`, `encrypt_key_value`, `hash_upstream_key` to the existing `janus.inventory.key_encryption` import if not already imported)

```python
async def swap_upstream_key_value(
    db_path: str | Path,
    key_id: str,
    *,
    previous: str,
    current: str,
) -> bool:
    if not previous or not current or previous == current:
        return False
    stored_value, key_hash, key_masked = _prepare_key_storage(current)
    async with get_connection(db_path) as db:
        async with db.execute(
            """UPDATE upstream_keys
               SET key_value = ?, key_hash = ?, key_masked = ?, updated_at = datetime('now')
               WHERE id = ? AND key_hash = ?""",
            (stored_value, key_hash, key_masked, key_id, hash_upstream_key(previous)),
        ) as cur:
            swapped = cur.rowcount == 1
        if swapped:
            async with db.execute(
                "SELECT source_node FROM upstream_keys WHERE id = ?", (key_id,)
            ) as cur:
                row = await cur.fetchone()
            source = str(row[0] or "") if row else ""
            if source.startswith("gateway:"):
                provider_id = source.removeprefix("gateway:")
                async with db.execute(
                    "SELECT api_key FROM providers WHERE id = ?", (provider_id,)
                ) as cur:
                    provider_row = await cur.fetchone()
                stored_provider_key = provider_row[0] if provider_row else None
                try:
                    provider_key = (
                        decrypt_key_value(str(stored_provider_key))
                        if stored_provider_key
                        else None
                    )
                except CredentialDecryptionError:
                    provider_key = None
                if provider_key is not None and provider_key.strip() == previous:
                    await db.execute(
                        "UPDATE providers SET api_key = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (encrypt_key_value(current), provider_id),
                    )
        await db.commit()
    return swapped
```

- [ ] **Step 4: Implement `inventory/credential_store.py`**

```python
from __future__ import annotations

import logging
from pathlib import Path

from janus.storage.upstream_keys import get_upstream_key, swap_upstream_key_value

logger = logging.getLogger(__name__)


class SqliteCredentialStore:
    def __init__(self, db_path: str | Path, upstream_key_id: str) -> None:
        self.db_path = db_path
        self.upstream_key_id = upstream_key_id

    async def load(self) -> str | None:
        try:
            row = await get_upstream_key(self.db_path, self.upstream_key_id)
        except Exception:
            logger.warning("Could not load credential for upstream key %s", self.upstream_key_id)
            return None
        if row is None or row.get("status") == "revoked":
            return None
        value = row.get("key_value")
        return value if isinstance(value, str) and value else None

    async def save(self, previous: str, current: str) -> bool:
        try:
            return await swap_upstream_key_value(
                self.db_path, self.upstream_key_id, previous=previous, current=current
            )
        except Exception:
            logger.warning(
                "Could not persist refreshed credential for upstream key %s",
                self.upstream_key_id,
            )
            return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_credential_store.py -v`
Expected: 7 passed. If `test_swap_updates_mirrored_provider_key` fails because `get_provider` masks the key, call `get_provider(db_path, "cl", include_secret=True)`.

- [ ] **Step 6: Commit**

```bash
git add src/janus/storage/upstream_keys.py src/janus/inventory/credential_store.py tests/unit/inventory/test_credential_store.py
git commit -m "feat(inventory): compare-and-swap credential store for refreshed OAuth tokens (#251)"
```

---

### Task 2: `PersistentCredentialMixin` + wire into the four OAuth executors

**Files:**
- Create: `src/janus/providers/credential_persistence.py`
- Modify: `src/janus/providers/claude_oauth.py` (`__init__`, `_ensure_token`)
- Modify: `src/janus/providers/codex.py` (`__init__`, `_ensure_token`)
- Modify: `src/janus/providers/kiro.py` (`__init__`, `_ensure_token`)
- Modify: `src/janus/providers/antigravity.py` (`__init__`, `_ensure_token`)
- Test: `tests/unit/providers/test_credential_persistence.py`

**Interfaces:**
- Consumes: nothing from Task 1 at import time (duck typing).
- Produces: `CredentialStore` protocol (`load() -> str | None`, `save(previous, current) -> bool`), `credential_expiry(cred) -> float | None`, `PersistentCredentialMixin` with `attach_credential_store(store)`, `_init_credential_persistence(api_key)`, `_adopt_newer_stored_credential() -> bool`, `_adopt_rotated_stored_credential() -> bool`, `_persist_credential() -> None`, module-level `_PENDING_SAVES: set[asyncio.Task[None]]`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
import respx
from httpx import Response

from janus.providers.claude_oauth import ClaudeOAuthProvider
from janus.providers.codex import CodexProvider
from janus.providers.credential_persistence import _PENDING_SAVES, credential_expiry
from janus.providers.oauth_tokens import CLAUDE_TOKEN_URL, CODEX_TOKEN_URL


class FakeStore:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.saves: list[tuple[str, str]] = []

    async def load(self) -> str | None:
        return self.value

    async def save(self, previous: str, current: str) -> bool:
        self.saves.append((previous, current))
        if previous != self.value:
            return False
        self.value = current
        return True


def _expired(**extra: Any) -> str:
    return json.dumps(
        {"access_token": "at-old", "refresh_token": "rt-old", "expires_at": time.time() - 10, **extra}
    )


async def _drain() -> None:
    while _PENDING_SAVES:
        await asyncio.gather(*list(_PENDING_SAVES))


def test_credential_expiry_normalizes_milliseconds() -> None:
    assert credential_expiry({"expiresAt": 1_790_000_000_000}) == pytest.approx(1_790_000_000.0)
    assert credential_expiry({"expires_at": "bad"}) is None
    assert credential_expiry({}) is None


@respx.mock
async def test_refresh_writes_back_once() -> None:
    blob = _expired()
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "refresh_token": "rt-new",
                                         "expires_in": 28800})
    )
    provider = ClaudeOAuthProvider(api_key=blob)
    store = FakeStore(blob)
    provider.attach_credential_store(store)
    assert await provider._ensure_token() is None
    await _drain()
    assert len(store.saves) == 1
    assert store.saves[0][0] == blob
    assert json.loads(store.value or "{}")["refresh_token"] == "rt-new"
    await provider.close()


@respx.mock
async def test_writeback_uses_exact_stored_string() -> None:
    blob = '{ "access_token" : "at-old", "refresh_token" : "rt-old", "expires_at" : 1 }'
    respx.post(CODEX_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "refresh_token": "rt-new",
                                         "expires_in": 3600})
    )
    provider = CodexProvider(api_key=blob)
    store = FakeStore(blob)
    provider.attach_credential_store(store)
    assert await provider._ensure_token() is None
    await _drain()
    assert store.saves and store.saves[0][0] == blob
    assert json.loads(store.value or "{}")["access_token"] == "at-new"
    await provider.close()


async def test_adopts_newer_stored_credential_without_refreshing() -> None:
    provider = ClaudeOAuthProvider(api_key=_expired())
    fresh = json.dumps(
        {"access_token": "at-fresh", "refresh_token": "rt-fresh", "expires_at": time.time() + 3600}
    )
    store = FakeStore(fresh)
    provider.attach_credential_store(store)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(CLAUDE_TOKEN_URL)
        assert await provider._ensure_token() is None
        assert not route.called
    assert "at-fresh" in provider.credential_blob()
    assert store.saves == []
    await provider.close()


@respx.mock
async def test_rejected_refresh_adopts_rotated_stored_credential() -> None:
    respx.post(CODEX_TOKEN_URL).mock(return_value=Response(400, json={"error": "invalid_grant"}))
    provider = CodexProvider(api_key=_expired())
    rotated = json.dumps({"access_token": "at-rot", "refresh_token": "rt-rot"})
    store = FakeStore(rotated)
    provider.attach_credential_store(store)
    result = await provider._ensure_token()
    assert result is None
    assert "at-rot" in provider.credential_blob()
    assert store.saves == []
    await provider.close()


@respx.mock
async def test_no_store_means_no_writeback_and_failure_is_not_fatal() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "expires_in": 60})
    )
    provider = ClaudeOAuthProvider(api_key=_expired())
    assert await provider._ensure_token() is None

    class BrokenStore(FakeStore):
        async def save(self, previous: str, current: str) -> bool:
            raise RuntimeError("db down")

    provider2 = ClaudeOAuthProvider(api_key=_expired())
    provider2.attach_credential_store(BrokenStore(None))
    assert await provider2._ensure_token() is None
    await _drain()
    await provider.close()
    await provider2.close()
```

Note: in `test_adopts_newer_stored_credential_without_refreshing` the stored blob has a later `expires_at`, so it is adopted before any refresh. In `test_rejected_refresh_adopts_rotated_stored_credential` the stored blob has no expiry (so it is not adopted up front), the refresh with `rt-old` is rejected, and the executor then adopts the stored credential because its refresh token differs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/providers/test_credential_persistence.py -v`
Expected: FAIL with `ModuleNotFoundError: janus.providers.credential_persistence`.

- [ ] **Step 3: Create `providers/credential_persistence.py`**

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .oauth_tokens import parse_credential, refresh_token, serialize_credential

logger = logging.getLogger(__name__)

_PENDING_SAVES: set[asyncio.Task[None]] = set()


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
```

- [ ] **Step 4: Wire `ClaudeOAuthProvider`** — class becomes `class ClaudeOAuthProvider(PersistentCredentialMixin):` (import from `.credential_persistence`); in `__init__` after `self._cred = parse_credential(api_key)` add `self._init_credential_persistence(api_key)`. Replace `_ensure_token` with:

```python
    async def _ensure_token(self) -> RawResult | None:
        if not needs_refresh(self._cred):
            return None
        if not refresh_token(self._cred):
            return None
        async with self._refresh_lock:
            if not needs_refresh(self._cred):
                return None
            if await self._adopt_newer_stored_credential() and not needs_refresh(self._cred):
                return None
            rt = refresh_token(self._cred)
            tokens = await refresh_claude(rt, self._client) if rt else None
            if tokens is None:
                if await self._adopt_rotated_stored_credential() and not needs_refresh(
                    self._cred
                ):
                    return None
                return RawResult(
                    status_code=401,
                    json_data={"error": "Claude OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            self._persist_credential()
        return None
```

- [ ] **Step 5: Wire `CodexProvider`** — same mixin base and `self._init_credential_persistence(api_key)` after `self._cred = parse_credential(api_key)`. Replace `_ensure_token` with:

```python
    async def _ensure_token(self) -> RawResult | None:
        if not needs_refresh(self._cred):
            return None
        if not refresh_token(self._cred):
            return None
        async with self._refresh_lock:
            if not needs_refresh(self._cred):
                return None
            if await self._adopt_newer_stored_credential() and not needs_refresh(self._cred):
                return None
            rt = refresh_token(self._cred)
            tokens = await refresh_codex(rt, self._client) if rt else None
            if tokens is None:
                if await self._adopt_rotated_stored_credential() and not needs_refresh(
                    self._cred
                ):
                    return None
                return RawResult(
                    status_code=401,
                    json_data={"error": "Codex OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            self._persist_credential()
        return None
```

- [ ] **Step 6: Wire `KiroProvider`** — mixin base; in `__init__` call `self._init_credential_persistence(api_key)` right after `self._cred` is assigned. Replace `_ensure_token` with:

```python
    def _kiro_should_refresh(self) -> bool:
        expires = self._cred.get("expires_at") or self._cred.get("expiresAt")
        return needs_refresh(self._cred) or (
            expires is None and bool(refresh_token(self._cred))
        )

    async def _ensure_token(self) -> RawResult | None:
        if self.auth_method == "api_key":
            return None
        if not self._kiro_should_refresh() or not refresh_token(self._cred):
            return None
        async with self._refresh_lock:
            if not self._kiro_should_refresh():
                return None
            if await self._adopt_newer_stored_credential() and not self._kiro_should_refresh():
                return None
            rt = refresh_token(self._cred)
            raw_extra = self._cred.get("extra")
            extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
            client_id = extra.get("clientId") or self._cred.get("clientId")
            client_secret = extra.get("clientSecret") or self._cred.get("clientSecret")
            region = str(extra.get("region") or self._cred.get("region") or self.region)
            if not rt:
                tokens = None
            elif client_id and client_secret:
                tokens = await refresh_kiro_aws(
                    rt,
                    self._client,
                    client_id=str(client_id),
                    client_secret=str(client_secret),
                    region=region,
                )
            else:
                tokens = await refresh_kiro_social(rt, self._client)
            if tokens is None:
                if (
                    await self._adopt_rotated_stored_credential()
                    and not self._kiro_should_refresh()
                ):
                    return None
                return RawResult(
                    status_code=401,
                    json_data={"error": "Kiro token refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            if tokens.get("profileArn"):
                next_extra = (
                    dict(self._cred.get("extra") or {})
                    if isinstance(self._cred.get("extra"), dict)
                    else {}
                )
                next_extra["profileArn"] = tokens["profileArn"]
                self._cred["extra"] = next_extra
            self._persist_credential()
        return None
```

Before editing, read the current `kiro.py::_ensure_token` in full and confirm the replacement preserves every branch (api_key auth, missing expiry, AWS vs social refresh, `profileArn` update). Keep any branch this plan omits.

- [ ] **Step 7: Wire `AntigravityProvider`** — mixin base; `self._init_credential_persistence(api_key)` after `self._cred` assignment. Replace `_ensure_token` with:

```python
    def _antigravity_should_refresh(self) -> bool:
        has_refresh = bool(refresh_token(self._cred))
        has_expiry = (
            self._cred.get("expires_at") is not None
            or self._cred.get("expiresAt") is not None
            or self.credential_expires_at is not None
        )
        return needs_refresh(self._cred) or (has_refresh and not has_expiry)

    async def _ensure_token(self) -> RawResult | None:
        if not self._antigravity_should_refresh() or not refresh_token(self._cred):
            return None
        async with self._refresh_lock:
            if not self._antigravity_should_refresh():
                return None
            if (
                await self._adopt_newer_stored_credential()
                and not self._antigravity_should_refresh()
            ):
                return None
            if self.variant in ("gemini_cli", "gemini-cli"):
                cid, csec = GOOGLE_CLI_CLIENT_ID, GOOGLE_CLI_CLIENT_SECRET
            else:
                cid, csec = ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
            rt = refresh_token(self._cred)
            tokens = (
                await refresh_google(rt, self._client, client_id=cid, client_secret=csec)
                if rt
                else None
            )
            if tokens is None:
                if (
                    await self._adopt_rotated_stored_credential()
                    and not self._antigravity_should_refresh()
                ):
                    return None
                return RawResult(
                    status_code=401,
                    json_data={"error": "Google OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            self._persist_credential()
        return None
```

- [ ] **Step 8: Run new + existing provider tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/providers tests/integration/test_provider_matrix.py -q`
Expected: all pass (existing Kiro/Antigravity/Codex/Claude refresh tests must stay green).

- [ ] **Step 9: Commit**

```bash
git add src/janus/providers tests/unit/providers/test_credential_persistence.py
git commit -m "feat(providers): read-through and write-back of refreshed OAuth credentials (#251)"
```

---

### Task 3: Attach stores in `reload_providers`

**Files:**
- Modify: `src/janus/dashboard/reload.py` (where `_build_provider(pc)` is called in `_reload_providers_locked`)
- Test: `tests/integration/test_credential_writeback_reload.py`

**Interfaces:**
- Consumes: `SqliteCredentialStore` (Task 1), `attach_credential_store` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import json
import time
from typing import Any

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.inventory.credential_store import SqliteCredentialStore
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


async def test_inventory_backed_oauth_provider_gets_credential_store(tmp_path: Any) -> None:
    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    db_path = app.state.db_path
    await init_db(db_path)
    await create_provider(
        db_path,
        {"id": "codex", "prefix": "codex", "api_type": "codex",
         "base_url": "https://chatgpt.com/backend-api/codex", "models": ["gpt-5.1-codex"]},
    )
    blob = json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600})
    key = await create_upstream_key(db_path, provider_id="codex", key_value=blob)
    await update_upstream_key(
        db_path, str(key["id"]),
        {"status": "active", "is_valid": 1, "is_usable": 1, "usability_status": "usable"},
    )

    await reload_providers(app)

    backed = [
        provider
        for provider in app.state.providers.values()
        if isinstance(getattr(provider, "_credential_store", None), SqliteCredentialStore)
    ]
    assert backed, "inventory-backed Codex provider should have a credential store"
    assert backed[0]._credential_store.upstream_key_id == str(key["id"])
```

If the key is not routable with these fields, copy the field set used by an existing reload test that routes an inventory Codex key (`grep -rn "list_routable_upstream_keys\|is_usable" tests/integration | head`).

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/integration/test_credential_writeback_reload.py -v`
Expected: FAIL on `assert backed`.

- [ ] **Step 3: Implement** — in `_reload_providers_locked`, replace

```python
                else:
                    provider = _build_provider(pc)
                    new_providers[pc.id] = provider
                    built_providers.append(provider)
```

with

```python
                else:
                    provider = _build_provider(pc)
                    attach = getattr(provider, "attach_credential_store", None)
                    if pc.upstream_key_id and callable(attach):
                        attach(SqliteCredentialStore(db_path, pc.upstream_key_id))
                    new_providers[pc.id] = provider
                    built_providers.append(provider)
```

and add `from janus.inventory.credential_store import SqliteCredentialStore` to the imports.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/integration/test_credential_writeback_reload.py tests/integration/test_provider_matrix.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/reload.py tests/integration/test_credential_writeback_reload.py
git commit -m "feat(routing): attach credential stores to inventory-backed providers (#251)"
```

---

### Task 4: Validators refresh only when needed (Codex, Antigravity)

**Files:**
- Modify: `src/janus/inventory/key_checker.py` (`_validate_codex_key`, `_validate_antigravity_key`)
- Test: `tests/unit/inventory/test_validator_refresh_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json
import time

import respx
from httpx import Response

from janus.inventory.key_checker import CODEX_RESPONSES_URL, _validate_codex_key
from janus.providers.oauth_tokens import CODEX_TOKEN_URL


def _codex(expires_at: float) -> str:
    return json.dumps(
        {"access_token": "at-live", "refresh_token": "rt-live", "expires_at": expires_at}
    )


@respx.mock
async def test_codex_validator_skips_refresh_when_access_token_valid() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(return_value=Response(200, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=Response(200, text="data: {}\n\n"))
    result = await _validate_codex_key(_codex(time.time() + 3600))
    assert result["is_valid"] is True
    assert not refresh.called
    assert json.loads(result["key_value"])["refresh_token"] == "rt-live"


@respx.mock
async def test_codex_validator_refreshes_expired_token() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "refresh_token": "rt-new",
                                         "expires_in": 3600})
    )
    result = await _validate_codex_key(_codex(time.time() - 10))
    assert refresh.called
    assert json.loads(result["key_value"])["access_token"] == "at-new"


@respx.mock
async def test_codex_validator_inconclusive_probe_does_not_refresh() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(return_value=Response(200, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=Response(503))
    result = await _validate_codex_key(_codex(time.time() + 3600))
    assert result.get("probe_inconclusive") is True
    assert not refresh.called
```

Add an Antigravity case in the same file, modelled on the existing Antigravity validator test (`grep -rn "_validate_antigravity_key" tests`): with a non-expired `expires_at`, assert the Google token URL (`GOOGLE_TOKEN_URL` from `janus.providers.oauth_tokens`) is **not** called and the loadCodeAssist probe is; with `expires_at` in the past, assert it **is** called.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_validator_refresh_policy.py -v`
Expected: `test_codex_validator_skips_refresh_when_access_token_valid` and the inconclusive test FAIL (refresh called).

- [ ] **Step 3: Implement Codex** — in `_validate_codex_key`, import `needs_refresh` alongside the existing imports, and directly after `rt = refresh_token(cred)` / the `if not rt:` block, before `async with httpx.AsyncClient(...)`, change the client block to start with:

```python
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        if access_token(cred) and not needs_refresh(cred):
            probe_status = await _probe_codex_access_token(client, cred)
            if probe_status is not None and probe_status < 400:
                return {
                    "is_valid": True,
                    "is_usable": True,
                    "usability_status": "usable",
                    "usability_note": "Access token valid; refresh left to the live provider",
                    "key_value": normalized,
                }
            if probe_status not in (401, 403):
                status_note = (
                    f"HTTP {probe_status}" if probe_status is not None else "probe unavailable"
                )
                return {
                    "probe_inconclusive": True,
                    "error": f"Codex access-token probe {status_note}",
                }
        tokens, refresh_error = await refresh_codex_detailed(rt, client)
```

(the rest of the existing block is unchanged).

- [ ] **Step 4: Implement Antigravity** — import `needs_refresh` and `credential_expiry` (`from janus.providers.credential_persistence import credential_expiry`); replace `if rt:` with:

```python
        rt = refresh_token(cred)
        should_refresh = bool(rt) and (needs_refresh(cred) or credential_expiry(cred) is None)
        if should_refresh:
```

keeping the existing body.

- [ ] **Step 5: Run validator tests + existing key checker tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory -q`
Expected: PASS. Existing Codex/Antigravity validator tests that assumed an unconditional refresh must be updated to use an expired `expires_at`; do not weaken their assertions otherwise.

- [ ] **Step 6: Commit**

```bash
git add src/janus/inventory/key_checker.py tests/unit/inventory
git commit -m "fix(inventory): validators refresh OAuth tokens only when expired (#251)"
```

---

### Task 5: Claude OAuth inventory onboarding (catalog, normalizer, detection, migration)

**Files:**
- Modify: `src/janus/catalog.py` (add `inventory` block to `claude_oauth`)
- Create: `src/janus/inventory/claude_credentials.py`
- Modify: `src/janus/inventory/ingestion.py`, `src/janus/inventory/url_guard.py`, `src/janus/dashboard/inventory_routes.py`
- Modify: `src/janus/storage/database.py` (migration)
- Test: `tests/unit/inventory/test_claude_credentials.py`, update `tests/unit/test_catalog.py`, `tests/unit/inventory/test_ingestion_classify.py`, `tests/integration/test_inventory_preview.py`

**Interfaces:**
- Produces: `normalize_claude_credential(raw: str) -> str` (Janus OAuth JSON: `access_token`, optional `refresh_token`, `expires_at` seconds, optional `extra.subscriptionType`)
- Produces: inventory provider id `claude_oauth`; `prefix_to_inventory_map()["claude"] == "claude_oauth"`.

- [ ] **Step 1: Write the failing tests** (`tests/unit/inventory/test_claude_credentials.py`)

```python
from __future__ import annotations

import json

import pytest

from janus.catalog import prefix_to_inventory_map
from janus.inventory.claude_credentials import normalize_claude_credential
from janus.inventory.ingestion import detect_credential_format
from janus.inventory.url_guard import detect_provider_from_key


def test_normalize_claude_code_file() -> None:
    raw = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-AAAA",
                "refreshToken": "sk-ant-ort01-BBBB",
                "expiresAt": 1790000000000,
                "scopes": ["user:inference"],
                "subscriptionType": "max",
                "email": "person@example.com",
            }
        }
    )
    out = json.loads(normalize_claude_credential(raw))
    assert out == {
        "access_token": "sk-ant-oat01-AAAA",
        "refresh_token": "sk-ant-ort01-BBBB",
        "expires_at": 1790000000.0,
        "extra": {"subscriptionType": "max"},
    }


def test_normalize_flat_json_and_bare_token() -> None:
    flat = json.loads(normalize_claude_credential('{"access_token": "sk-ant-oat01-X", "refresh_token": "r"}'))
    assert flat == {"access_token": "sk-ant-oat01-X", "refresh_token": "r"}
    bare = json.loads(normalize_claude_credential("  sk-ant-oat01-BARE  "))
    assert bare == {"access_token": "sk-ant-oat01-BARE"}


@pytest.mark.parametrize("raw", ["", "{}", '{"claudeAiOauth": {}}', "[1]"])
def test_normalize_rejects_missing_access_token(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_claude_credential(raw)


def test_detect_provider_from_key_claude_oauth() -> None:
    assert detect_provider_from_key("sk-ant-oat01-abcdef") == "claude_oauth"
    assert detect_provider_from_key("sk-ant-api03-abcdef") == "anthropic"


def test_claude_code_file_detected_as_claude_oauth() -> None:
    raw = json.dumps({"claudeAiOauth": {"accessToken": "a"}})
    assert detect_credential_format(raw) == ("oauth_json", "claude_oauth")


def test_claude_prefix_maps_to_inventory() -> None:
    assert prefix_to_inventory_map()["claude"] == "claude_oauth"
```

Update existing tests:
- `tests/unit/inventory/test_ingestion_classify.py`: expected `("oauth_json", "claude_code")` → `("oauth_json", "claude_oauth")`.
- `tests/integration/test_inventory_preview.py::test_claude_code_credentials_rejected_with_specific_reason` → rename to `test_claude_code_credentials_are_accepted` and assert preview `status == "new"`, `provider_id == "claude_oauth"`, `format == "oauth_json"`, that neither response text contains `SECRET`, and that submit registers one key (copy the submit-assertion pattern from the Codex test in the same file).
- `tests/unit/test_catalog.py`: `inventory_entries()` count +1 (read the current number and add one); remove `"claude_oauth"` from `test_gateway_only_entries_have_no_inventory_block` and from the exception tuple in `test_new_9router_providers_present`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_claude_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: janus.inventory.claude_credentials`.

- [ ] **Step 3: Create `inventory/claude_credentials.py`**

```python
"""Normalize Claude Code OAuth credentials for Key Inventory."""

from __future__ import annotations

import json
from typing import Any

from janus.providers.credential_persistence import credential_expiry


def _string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_claude_credential(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Empty Claude credential")
    if not text.startswith(("{", "[")):
        return json.dumps({"access_token": text})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Claude credential JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Claude credential JSON must be an object")
    nested = data.get("claudeAiOauth")
    source = nested if isinstance(nested, dict) else data
    access = _string(source, "access_token", "accessToken")
    if not access:
        raise ValueError("Claude credential missing access token")
    out: dict[str, Any] = {"access_token": access}
    refresh = _string(source, "refresh_token", "refreshToken")
    if refresh:
        out["refresh_token"] = refresh
    expires = credential_expiry(source)
    if expires is not None:
        out["expires_at"] = expires
    subscription = _string(source, "subscriptionType", "subscription_type")
    if subscription:
        out["extra"] = {"subscriptionType": subscription}
    return json.dumps(out)
```

(`credential_expiry` handles both `expires_at` and `expiresAt` and converts milliseconds.)

- [ ] **Step 4: Catalog** — in `catalog.py`, add to the `"claude_oauth"` entry, before `"gateway"`:

```python
        "inventory": {
            "id": "claude_oauth",
            "name": "claude_oauth",
            "display_name": "Claude Code (OAuth)",
            "base_url": "https://api.anthropic.com",
            "auth_type": "oauth",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "subscription",
            "is_direct": True,
            "routing_note": "Drop Claude Code's ~/.claude/.credentials.json, a JSON object "
            "with access_token and refresh_token, or a bare sk-ant-oat access token.",
        },
```

Check `docs/inventory.md` and any catalog test that asserts the inventory field set and match it.

- [ ] **Step 5: Detection** — `url_guard.detect_provider_from_key`: insert before the `sk-ant-` rule:

```python
    if key.startswith("sk-ant-oat"):
        return "claude_oauth"
```

`ingestion.py`:
- delete `CLAUDE_CODE_UNSUPPORTED_ERROR` and `_CLAUDE_CODE_MARKER`; in `detect_credential_json_provider` return `"claude_oauth"` for `claudeAiOauth`; in `_OAUTH_FORMAT_BY_PROVIDER` replace the marker key with `"claude_oauth": "oauth_json"`;
- `_TOKEN_CREDENTIAL_PROVIDERS = frozenset({"codex", "antigravity", "cline", "claude_oauth"})`;
- remove the `if detected == _CLAUDE_CODE_MARKER: return rejected(...)` block in `classify_upstream_entry`;
- in `_normalize_key_value` add before the JSON fallthrough:

```python
    if provider_id == "claude_oauth":
        return normalize_claude_credential(raw_key)
```

with `from janus.inventory.claude_credentials import normalize_claude_credential`.

`dashboard/inventory_routes.py`: remove the `CLAUDE_CODE_UNSUPPORTED_ERROR` import and its use in the error-message mapping (`if value in {UNSUPPORTED_FORMAT_ERROR}:`).

- [ ] **Step 6: Migration** — add to `storage/database.py` (reuse `_table_columns` from the Cursor removal; if absent on this branch, add it):

```python
async def _migrate_claude_oauth_inventory(db: aiosqlite.Connection) -> None:
    if not {"api_type"} <= await _table_columns(db, "providers"):
        return
    if not {"provider_id", "source_node"} <= await _table_columns(db, "upstream_keys"):
        return
    await db.execute(
        """UPDATE upstream_keys SET provider_id = 'claude_oauth'
           WHERE provider_id = 'claude'
             AND source_node IN (
               SELECT 'gateway:' || id FROM providers
               WHERE api_type IN ('claude_oauth', 'claude')
             )"""
    )
    await db.execute(
        """DELETE FROM inventory_providers
           WHERE id = 'claude'
             AND routing_note = 'Mirrored from Providers page'
             AND NOT EXISTS (SELECT 1 FROM upstream_keys WHERE provider_id = 'claude')"""
    )
```

Call it in `init_db` after `_disable_removed_provider_types(db)` (or after `_backfill_request_outcomes(db)` if that function is absent). Add a test to `tests/unit/inventory/test_claude_credentials.py`:

```python
async def test_migration_repoints_mirrored_claude_keys(tmp_path) -> None:
    from janus.inventory.provider_key_sync import _upsert_custom_inventory_provider
    from janus.storage.database import init_db
    from janus.storage.providers_db import create_provider
    from janus.storage.upstream_keys import create_upstream_key, get_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    row = {"id": "cl", "prefix": "claude", "api_type": "claude_oauth",
           "base_url": "https://api.anthropic.com", "api_key": "sk-ant-oat01-x", "models": ["m"]}
    await create_provider(db_path, row)
    await _upsert_custom_inventory_provider(db_path, row, "claude")
    mirrored = await create_upstream_key(
        db_path, provider_id="claude", key_value="sk-ant-oat01-x", source_node="gateway:cl"
    )
    other = await create_upstream_key(db_path, provider_id="claude", key_value="sk-other-key-1")

    await init_db(db_path)
    await init_db(db_path)

    moved = await get_upstream_key(db_path, str(mirrored["id"]))
    untouched = await get_upstream_key(db_path, str(other["id"]))
    assert moved is not None and moved["provider_id"] == "claude_oauth"
    assert untouched is not None and untouched["provider_id"] == "claude"
```

- [ ] **Step 7: Run tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory tests/unit/test_catalog.py tests/integration/test_inventory_preview.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/janus/catalog.py src/janus/inventory src/janus/dashboard/inventory_routes.py src/janus/storage/database.py tests
git commit -m "feat(inventory): onboard Claude OAuth credentials into Key Inventory (#251)"
```

---

### Task 6: Claude OAuth validator

**Files:**
- Modify: `src/janus/providers/oauth_tokens.py` (constants)
- Modify: `src/janus/inventory/key_checker.py` (`_validate_claude_oauth_key`, dispatch)
- Test: `tests/unit/inventory/test_claude_validator.py`

**Interfaces:**
- Produces: `CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"`, `CLAUDE_OAUTH_BETA = "oauth-2025-04-20"` in `oauth_tokens.py`; `def claude_usage_headers(token: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json
import time

import httpx
import respx
from httpx import Response

from janus.inventory.key_checker import validate_key
from janus.providers.oauth_tokens import CLAUDE_TOKEN_URL, CLAUDE_USAGE_URL


def _cred(expires_at: float) -> str:
    return json.dumps(
        {"access_token": "sk-ant-oat01-live", "refresh_token": "sk-ant-ort01-live",
         "expires_at": expires_at}
    )


@respx.mock
async def test_valid_token_is_usable_without_refresh() -> None:
    refresh = respx.post(CLAUDE_TOKEN_URL).mock(return_value=Response(200, json={}))
    usage = respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(200, json={"five_hour": {}}))
    result = await validate_key(_cred(time.time() + 3600), "claude_oauth")
    assert result["is_valid"] is True and result["is_usable"] is True
    assert not refresh.called
    assert usage.calls.last.request.headers["anthropic-beta"] == "oauth-2025-04-20"


@respx.mock
async def test_expired_token_refreshes_once_and_returns_blob() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "sk-ant-oat01-new",
                                         "refresh_token": "sk-ant-ort01-new", "expires_in": 28800})
    )
    respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(200, json={}))
    result = await validate_key(_cred(time.time() - 10), "claude_oauth")
    assert json.loads(result["key_value"])["access_token"] == "sk-ant-oat01-new"


@respx.mock
async def test_rejected_token_is_invalid() -> None:
    respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(401))
    result = await validate_key(_cred(time.time() + 3600), "claude_oauth")
    assert result["is_valid"] is False
    assert "sk-ant" not in json.dumps(result.get("error"))


@respx.mock
async def test_rate_limit_and_network_are_inconclusive() -> None:
    route = respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(429))
    assert (await validate_key(_cred(time.time() + 3600), "claude_oauth"))["probe_inconclusive"]
    route.side_effect = httpx.ConnectError("down")
    assert (await validate_key(_cred(time.time() + 3600), "claude_oauth"))["probe_inconclusive"]


@respx.mock
async def test_failed_refresh_is_invalid() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(return_value=Response(400, json={"error": "invalid_grant"}))
    result = await validate_key(_cred(time.time() - 10), "claude_oauth")
    assert result["is_valid"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_claude_validator.py -v`
Expected: FAIL with `ImportError: CLAUDE_USAGE_URL`.

- [ ] **Step 3: Constants** — in `oauth_tokens.py` below `CLAUDE_AUTHORIZE_URL`:

```python
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"


def claude_usage_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "anthropic-beta": CLAUDE_OAUTH_BETA,
    }
```

- [ ] **Step 4: Validator** — add to `key_checker.py` and dispatch `if provider_id == "claude_oauth": return await _validate_claude_oauth_key(key_value, metadata)` next to the Kiro dispatch:

```python
async def _validate_claude_oauth_key(
    key_value: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del metadata
    from janus.inventory.claude_credentials import normalize_claude_credential
    from janus.providers.oauth_tokens import (
        CLAUDE_USAGE_URL,
        access_token,
        apply_token_response,
        claude_usage_headers,
        needs_refresh,
        parse_credential,
        refresh_claude,
        refresh_token,
        serialize_credential,
    )

    try:
        normalized = normalize_claude_credential(key_value)
    except ValueError as exc:
        return {"is_valid": False, "error": str(exc)}
    cred = parse_credential(normalized)
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        rt = refresh_token(cred)
        if rt and needs_refresh(cred):
            try:
                tokens = await refresh_claude(rt, client)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                return {"probe_inconclusive": True, "error": f"Claude refresh unavailable: {exc}"}
            if tokens is None:
                return {"is_valid": False, "error": "Claude OAuth refresh failed; re-export"}
            cred = apply_token_response(cred, tokens)
            normalized = serialize_credential(cred)
        token = access_token(cred)
        if not token:
            return {"is_valid": False, "error": "Claude credential missing access token"}
        try:
            response = await client.get(CLAUDE_USAGE_URL, headers=claude_usage_headers(token))
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            return {"probe_inconclusive": True, "error": f"Claude usage probe unavailable: {exc}"}
    if response.status_code == 200:
        return {
            "is_valid": True,
            "is_usable": True,
            "usability_status": "usable",
            "usability_note": "Claude OAuth usage endpoint accepted the token",
            "key_value": normalized,
        }
    if response.status_code in (401, 403):
        return {"is_valid": False, "error": f"Claude OAuth token rejected ({response.status_code})"}
    return {"probe_inconclusive": True, "error": f"Claude usage probe HTTP {response.status_code}"}
```

`str(exc)` from httpx errors never contains the bearer token; confirm `test_rejected_token_is_invalid` covers error text.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_claude_validator.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/janus/providers/oauth_tokens.py src/janus/inventory/key_checker.py tests/unit/inventory/test_claude_validator.py
git commit -m "feat(inventory): validate Claude OAuth credentials via the usage endpoint (#251)"
```

---

### Task 7: Claude account-value probe

**Files:**
- Modify: `src/janus/inventory/account_value.py`
- Test: `tests/unit/inventory/test_account_value_oauth.py` (extend)

- [ ] **Step 1: Write the failing tests** (append; reuse the module's `_cred` helper and autouse private-URL fixture)

```python
CLAUDE_URL = "https://api.anthropic.com/api/oauth/usage"


@respx.mock
async def test_claude_probe_maps_windows() -> None:
    route = respx.get(CLAUDE_URL).mock(
        return_value=Response(
            200,
            json={
                "five_hour": {"utilization": 42.0, "resets_at": "2026-10-01T12:00:00+00:00"},
                "seven_day": {"utilization": 91, "resets_at": "2026-10-05T00:00:00+00:00"},
                "seven_day_opus": {"utilization": 12.5, "resets_at": None},
                "seven_day_sonnet": None,
                "email": "person@example.com",
            },
        )
    )
    value = await probe_account_value(
        "claude_oauth", _cred(extra={"subscriptionType": "max"}), "https://api.anthropic.com", None
    )
    assert value.status == AccountValueStatus.OK
    assert [(w.label, w.used_percent) for w in value.windows] == [
        ("5h", 42.0), ("weekly", 91.0), ("weekly opus", 12.5)
    ]
    assert value.metadata == {"subscription_type": "max"}
    assert "person@example.com" not in json.dumps(value.to_dict())
    assert route.calls.last.request.headers["anthropic-beta"] == "oauth-2025-04-20"


@respx.mock
async def test_claude_probe_unauthorized_is_probe_error() -> None:
    respx.get(CLAUDE_URL).mock(return_value=Response(401))
    with pytest.raises(ProbeError):
        await probe_account_value("claude_oauth", _cred(), "https://api.anthropic.com", None)


async def test_claude_probe_expired_token_does_not_call_endpoint() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(CLAUDE_URL)
        with pytest.raises(ProbeError):
            await probe_account_value(
                "claude_oauth", _cred(expires_at=time.time() - 5),
                "https://api.anthropic.com", None,
            )
        assert not route.called


@respx.mock
async def test_claude_probe_empty_response_is_transient_error() -> None:
    respx.get(CLAUDE_URL).mock(return_value=Response(200, json={"five_hour": None}))
    with pytest.raises(ProbeError):
        await probe_account_value("claude_oauth", _cred(), "https://api.anthropic.com", None)
```

Also extend `test_oauth_providers_registered` to include `"claude_oauth"`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_account_value_oauth.py -v -k claude`
Expected: FAIL (`claude_oauth` probe returns UNSUPPORTED / not registered).

- [ ] **Step 3: Implement** — extend the import `from janus.providers.oauth_tokens import CLAUDE_USAGE_URL, access_token, claude_usage_headers, parse_credential`, and add before `AccountValueProbe = ...`:

```python
_CLAUDE_WINDOWS = (
    ("five_hour", "5h"),
    ("seven_day", "weekly"),
    ("seven_day_opus", "weekly opus"),
    ("seven_day_sonnet", "weekly sonnet"),
)


async def _probe_claude_oauth(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    del base_url, custom_base_url
    token, cred = _oauth_access_token(key_value)
    body = await _fetch_oauth_json(CLAUDE_USAGE_URL, headers=claude_usage_headers(token))
    windows: list[UsageWindow] = []
    for field_name, label in _CLAUDE_WINDOWS:
        row = _child(body, field_name)
        percent = _percent(row.get("utilization"))
        if percent is None:
            continue
        windows.append(
            UsageWindow(label=label, used_percent=percent, reset_at=_reset_at(row.get("resets_at")))
        )
    if not windows:
        raise ProbeError("claude usage response had no recognized windows", transient=True)
    metadata: dict[str, Any] = {}
    subscription = _cred_extra(cred).get("subscriptionType")
    if isinstance(subscription, str) and subscription:
        metadata["subscription_type"] = subscription
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("claude_oauth", "oauth-usage"),
        fetched_at=_now_iso(),
        windows=windows,
        metadata=metadata,
    )
```

and register `"claude_oauth": _probe_claude_oauth,` in `ACCOUNT_VALUE_PROBES`. Confirm `_child` returns `{}` for `None`/non-dict values (it does for the Codex probe); if not, guard with `isinstance(body.get(field_name), dict)`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/unit/inventory/test_account_value_oauth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/inventory/account_value.py tests/unit/inventory/test_account_value_oauth.py
git commit -m "feat(inventory): Claude OAuth account-value probe via /api/oauth/usage (#251)"
```

---

### Task 8: Dashboard hint, docs, contracts, changelog, full gates

**Files:**
- Modify: `dashboard-ui/src/lib/pages/ProvidersPage.svelte` (Claude credential hint), rebuilt bundle under `src/janus/dashboard/static/app/`
- Modify: `docs/inventory.md` (formats table, line ~88 Claude paragraph), `docs/dashboard.md` (Connect auto-detect list ~line 122), `CHANGELOG.md`
- Regenerate: `dashboard-ui/src/lib/contract-fixtures/*.shape.json` affected by the catalog change

- [ ] **Step 1: Providers page hint** — replace the `claude`/`claude_oauth` case text with:

```ts
        return 'Drop Claude Code’s ~/.claude/.credentials.json on Connect (or paste a JSON object with access_token and refresh_token). It is stored as an Inventory credential and refreshed automatically.';
```

- [ ] **Step 2: Docs** — in `docs/inventory.md` replace the "Claude Code `~/.claude/.credentials.json` is recognized but not stored…" paragraph with a table row:

```markdown
| Claude Code `~/.claude/.credentials.json` | **Auto** or **Claude Code (OAuth)** | The `claudeAiOauth` object (access token, refresh token, expiry, subscription type) becomes a Claude OAuth credential. A bare `sk-ant-oat…` access token also works. Janus refreshes it and saves the refreshed token. |
```

Add Claude Code to the auto-recognized list in `docs/dashboard.md` and add a short "Claude OAuth" line to the account-value probe section of `docs/inventory.md` (5h / weekly / per-model weekly windows, 10-minute cache). Add to `CHANGELOG.md` under `[Unreleased]`:

```markdown
### Added
- **Claude OAuth in Key Inventory** — Connect now accepts Claude Code's
  `.credentials.json` (and bare `sk-ant-oat…` tokens) as `claude_oauth`
  inventory credentials, validates them against `/api/oauth/usage`, and probes
  5h / weekly / per-model weekly windows for the keys table and routing
  soft-ordering. Existing Providers-page Claude OAuth keys move to the new
  inventory provider on startup. (#251)
### Fixed
- **Refreshed OAuth tokens are saved** — Claude, Codex, Kiro and Antigravity
  executors now write refreshed credentials back to their inventory row
  (compare-and-swap) and re-read the stored credential before refreshing, and
  validators refresh only expired tokens. Rotating refresh tokens no longer
  break after a restart or a validator run. (#251)
```

- [ ] **Step 3: Contracts**

Run: `JANUS_REGEN_CONTRACT_FIXTURES=1 PYTHONPATH=$PWD/src:$PWD ../Janus/.venv/bin/python -m pytest tests/integration/test_dashboard_state_contracts.py -q`
Then `git diff --stat dashboard-ui/src/lib/contract-fixtures`. Keep only diffs caused by the `claude_oauth` inventory block; revert pure key-order churn (`git checkout <file>`). If a byte-pinned (non-`.shape.json`) fixture changes meaningfully, update `dashboard-ui/src/lib/contracts.ts` in the same commit.

- [ ] **Step 4: Full gates**

```bash
PY=../Janus/.venv/bin/python; export PYTHONPATH=$PWD/src:$PWD
$PY -m ruff check src/janus tests scripts && $PY -m ruff format --check src/janus tests scripts
$PY -m mypy src/janus scripts
$PY -m pytest -q
$PY scripts/build_dashboard_ui.py && $PY scripts/build_dashboard_ui.py --check
$PY -m mkdocs build --strict && rm -rf site
$PY scripts/migration_smoke.py
```

Expected: all green. Do not symlink `dashboard-ui/node_modules`; the build script runs `npm ci` in the worktree.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: Claude OAuth inventory onboarding, write-back changelog, contracts (#251)"
```
