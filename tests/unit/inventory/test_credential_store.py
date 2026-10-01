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
        {
            "id": "cl",
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": OLD,
            "models": ["m"],
        },
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
        {
            "id": "cl",
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": "user-edited",
            "models": ["m"],
        },
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


async def test_swap_matches_reformatted_previous(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    stored = '{"access_token":"at-old","refresh_token":"rt-old"}'
    key = await create_upstream_key(db_path, provider_id="codex", key_value=stored)
    reformatted = '{ "access_token": "at-old", "refresh_token": "rt-old" }'
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=reformatted, current=NEW)
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == NEW


async def test_swap_refuses_semantically_different_previous(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    different = '{"access_token": "at-old", "refresh_token": "rt-other"}'
    assert not await swap_upstream_key_value(
        db_path, str(key["id"]), previous=different, current=NEW
    )
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == OLD


async def test_swap_updates_mirror_when_provider_key_is_reformatted(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    provider_key = '{ "access_token": "at-old",  "refresh_token": "rt-old" }'
    await create_provider(
        db_path,
        {
            "id": "cl",
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": provider_key,
            "models": ["m"],
        },
    )
    key = await create_upstream_key(
        db_path, provider_id="claude_oauth", key_value=OLD, source_node="gateway:cl"
    )
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)
    provider = await get_provider(db_path, "cl")
    assert provider is not None and provider["api_key"] == NEW


async def test_swap_second_writer_with_stale_previous_loses(tmp_path: Any) -> None:
    db_path = await _db(tmp_path)
    key = await create_upstream_key(db_path, provider_id="codex", key_value=OLD)
    other = '{"access_token":"at-other","refresh_token":"rt-other"}'
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)
    assert not await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=other)
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == NEW


async def test_swap_updates_encrypted_mirrored_provider_key(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from cryptography.fernet import Fernet

    from janus.inventory.key_encryption import is_encrypted_value
    from janus.storage.database import get_connection

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db_path = await _db(tmp_path)
    await create_provider(
        db_path,
        {
            "id": "cl",
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": OLD,
            "models": ["m"],
        },
    )
    key = await create_upstream_key(
        db_path, provider_id="claude_oauth", key_value=OLD, source_node="gateway:cl"
    )
    assert await swap_upstream_key_value(db_path, str(key["id"]), previous=OLD, current=NEW)

    async with get_connection(db_path) as db:
        async with db.execute("SELECT api_key FROM providers WHERE id = 'cl'") as cur:
            stored = await cur.fetchone()
    assert stored is not None and is_encrypted_value(str(stored[0]))
    assert NEW not in str(stored[0])
    provider = await get_provider(db_path, "cl")
    assert provider is not None and provider["api_key"] == NEW
    row = await get_upstream_key(db_path, str(key["id"]))
    assert row is not None and row["key_value"] == NEW
