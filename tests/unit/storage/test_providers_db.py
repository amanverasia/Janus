import json

import pytest
from cryptography.fernet import Fernet

from janus.inventory.key_encryption import (
    ENCRYPTED_PREFIX,
    CredentialDecryptionError,
    encrypt_key_value,
)
from janus.storage.database import get_connection, init_db
from janus.storage.providers_db import (
    count_provider_encryption_state,
    create_provider,
    delete_provider,
    get_provider,
    list_providers,
    reencrypt_plaintext_provider_keys,
    toggle_provider,
    update_provider,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_provider(db):
    await create_provider(
        db,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-xxx",
            "models": ["gpt-4o", "gpt-4o-mini"],
        },
    )
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "openai"
    assert providers[0]["is_enabled"] == 1
    assert json.loads(providers[0]["models"]) == ["gpt-4o", "gpt-4o-mini"]


async def test_get_provider(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://test.local",
            "api_key": None,
            "models": [],
        },
    )
    p = await get_provider(db, "test")
    assert p["id"] == "test"
    assert p["api_key"] is None


async def test_get_provider_not_found(db):
    assert await get_provider(db, "nonexistent") is None


async def test_update_provider(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://old.local",
            "api_key": "old",
            "models": ["m1"],
        },
    )
    await update_provider(
        db,
        "test",
        {
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://new.local",
            "api_key": "new",
            "models": ["m1", "m2"],
        },
    )
    p = await get_provider(db, "test")
    assert p["base_url"] == "https://new.local"
    assert json.loads(p["models"]) == ["m1", "m2"]


async def test_toggle_provider(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://test.local",
            "api_key": None,
            "models": [],
        },
    )
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 0
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 1


async def test_delete_provider(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://test.local",
            "api_key": None,
            "models": [],
        },
    )
    await delete_provider(db, "test")
    assert await get_provider(db, "test") is None


async def test_create_provider_with_allowed_models(db):
    await create_provider(
        db,
        {
            "id": "anthropic",
            "prefix": "an",
            "api_type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-xxx",
            "models": ["claude-opus-4-7", "claude-sonnet-4-5"],
            "allowed_models": ["claude-opus-4-7"],
        },
    )
    p = await get_provider(db, "anthropic")
    assert json.loads(p["allowed_models"]) == ["claude-opus-4-7"]


async def test_create_provider_without_allowed_models_defaults_empty(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://test.local",
            "api_key": None,
            "models": [],
        },
    )
    p = await get_provider(db, "test")
    assert json.loads(p["allowed_models"]) == []


async def test_update_provider_allowed_models(db):
    await create_provider(
        db,
        {
            "id": "test",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://old.local",
            "api_key": "old",
            "models": ["m1"],
        },
    )
    await update_provider(
        db,
        "test",
        {
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://new.local",
            "api_key": "new",
            "models": ["m1", "m2"],
            "allowed_models": ["m1"],
        },
    )
    p = await get_provider(db, "test")
    assert json.loads(p["allowed_models"]) == ["m1"]


async def test_list_providers_only_enabled(db):
    await create_provider(
        db,
        {
            "id": "a",
            "prefix": "a",
            "api_type": "openai_compat",
            "base_url": "https://a.local",
            "api_key": None,
            "models": [],
        },
    )
    await create_provider(
        db,
        {
            "id": "b",
            "prefix": "b",
            "api_type": "openai_compat",
            "base_url": "https://b.local",
            "api_key": None,
            "models": [],
        },
    )
    await toggle_provider(db, "b")
    enabled = await list_providers(db, enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0]["id"] == "a"
    all_p = await list_providers(db, enabled_only=False)
    assert len(all_p) == 2


async def _raw_api_key(db, provider_id: str):
    async with get_connection(db) as conn:
        async with conn.execute(
            "SELECT api_key FROM providers WHERE id = ?", (provider_id,)
        ) as cur:
            row = await cur.fetchone()
    return row["api_key"]


async def test_provider_credentials_encrypt_at_rest_and_decrypt_on_read(db, monkeypatch):
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await create_provider(
        db,
        {
            "id": "secure",
            "prefix": "secure",
            "api_type": "openai_compat",
            "base_url": "https://secure.local",
            "api_key": "sk-secret",
            "models": [],
        },
    )

    stored = await _raw_api_key(db, "secure")
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert "sk-secret" not in stored
    assert (await get_provider(db, "secure"))["api_key"] == "sk-secret"
    assert (await list_providers(db))[0]["api_key"] == "sk-secret"


async def test_update_provider_encrypts_oauth_blob_opaquely(db, monkeypatch):
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await create_provider(
        db,
        {
            "id": "oauth",
            "prefix": "oauth",
            "api_type": "codex",
            "base_url": "https://oauth.local",
            "api_key": None,
            "models": [],
        },
    )
    credential = json.dumps({"access_token": "access", "refresh_token": "refresh"})
    await update_provider(
        db,
        "oauth",
        {
            "prefix": "oauth",
            "api_type": "codex",
            "base_url": "https://oauth.local",
            "api_key": credential,
            "models": [],
        },
    )

    assert (await _raw_api_key(db, "oauth")).startswith(ENCRYPTED_PREFIX)
    assert (await get_provider(db, "oauth"))["api_key"] == credential


async def test_provider_credentials_preserve_none_and_empty(db, monkeypatch):
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for provider_id, value in (("none", None), ("empty", "")):
        await create_provider(
            db,
            {
                "id": provider_id,
                "prefix": provider_id,
                "api_type": "openai_compat",
                "base_url": f"https://{provider_id}.local",
                "api_key": value,
                "models": [],
            },
        )
        assert await _raw_api_key(db, provider_id) == value


async def test_provider_credentials_migrate_plaintext_idempotently(db, monkeypatch):
    await create_provider(
        db,
        {
            "id": "legacy",
            "prefix": "legacy",
            "api_type": "openai_compat",
            "base_url": "https://legacy.local",
            "api_key": "sk-legacy",
            "models": [],
        },
    )
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

    assert await count_provider_encryption_state(db) == {
        "encrypted": 0,
        "plaintext": 1,
        "total": 1,
    }
    assert await reencrypt_plaintext_provider_keys(db) == 1
    assert await reencrypt_plaintext_provider_keys(db) == 0
    assert await count_provider_encryption_state(db) == {
        "encrypted": 1,
        "plaintext": 0,
        "total": 1,
    }
    assert (await get_provider(db, "legacy"))["api_key"] == "sk-legacy"


async def test_encrypted_provider_requires_matching_key(db, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", key)
    await create_provider(
        db,
        {
            "id": "secure",
            "prefix": "secure",
            "api_type": "openai_compat",
            "base_url": "https://secure.local",
            "api_key": "sk-secret",
            "models": [],
        },
    )

    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY")
    with pytest.raises(CredentialDecryptionError, match="required to decrypt stored credentials"):
        await get_provider(db, "secure")

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(CredentialDecryptionError, match="Failed to decrypt stored credential"):
        await get_provider(db, "secure")


def test_encrypt_key_value_still_accepts_plaintext_legacy_values(monkeypatch):
    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY", raising=False)
    assert encrypt_key_value("sk-plain") == "sk-plain"
