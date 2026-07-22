import pytest

from janus.inventory.key_encryption import (
    ENCRYPTED_PREFIX,
    CredentialDecryptionError,
    CredentialEncryptionError,
    decrypt_key_value,
    encrypt_key_value,
    generate_encryption_key,
    hash_upstream_key,
    is_encrypted_value,
)
from janus.storage.database import init_db
from janus.storage.upstream_keys import (
    count_storage_encryption_state,
    create_upstream_key,
    find_upstream_key_by_value,
    find_upstream_key_by_value_and_provider,
    get_upstream_key,
    reencrypt_plaintext_upstream_keys,
)


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = generate_encryption_key()
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", key)
    return key


@pytest.mark.asyncio
async def test_encrypt_decrypt_round_trip(encryption_key: str) -> None:
    plaintext = "sk-proj-test-encryption-round-trip"
    stored = encrypt_key_value(plaintext)
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert decrypt_key_value(stored) == plaintext


@pytest.mark.asyncio
async def test_plaintext_fallback_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY", raising=False)
    plaintext = "sk-proj-plaintext-fallback"
    assert encrypt_key_value(plaintext) == plaintext
    assert decrypt_key_value(plaintext) == plaintext
    assert not is_encrypted_value(plaintext)


def test_encrypted_value_requires_matching_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = generate_encryption_key()
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", key)
    stored = encrypt_key_value("sk-secret")

    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY")
    with pytest.raises(CredentialDecryptionError, match="required to decrypt stored credentials"):
        decrypt_key_value(stored)

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", generate_encryption_key())
    with pytest.raises(CredentialDecryptionError, match="Failed to decrypt stored credential"):
        decrypt_key_value(stored)

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(CredentialDecryptionError, match="invalid; expected a Fernet key"):
        decrypt_key_value(stored)
    with pytest.raises(CredentialEncryptionError, match="invalid; expected a Fernet key"):
        encrypt_key_value("sk-new-secret")


@pytest.mark.asyncio
async def test_create_upstream_key_encrypts_at_rest(tmp_path, encryption_key: str) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    plaintext = "sk-proj-encrypted-at-rest-key"
    record = await create_upstream_key(db_path, provider_id="openai", key_value=plaintext)

    fetched = await get_upstream_key(db_path, record["id"])
    assert fetched is not None
    assert fetched["key_value"] == plaintext

    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key_value, key_hash FROM upstream_keys WHERE id = ?",
            (record["id"],),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["key_value"].startswith(ENCRYPTED_PREFIX)
    assert row["key_hash"] == hash_upstream_key(plaintext)


@pytest.mark.asyncio
async def test_find_upstream_key_by_hash(tmp_path, encryption_key: str) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    plaintext = "sk-proj-hash-lookup-key-value"
    await create_upstream_key(db_path, provider_id="openai", key_value=plaintext)

    found = await find_upstream_key_by_value(db_path, plaintext)
    assert found is not None
    assert found["key_value"] == plaintext

    by_provider = await find_upstream_key_by_value_and_provider(
        db_path,
        plaintext,
        "openai",
    )
    assert by_provider is not None
    assert by_provider["provider_id"] == "openai"


@pytest.mark.asyncio
async def test_reencrypt_plaintext_upstream_keys(tmp_path, encryption_key: str) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    plaintext = "sk-proj-reencrypt-plaintext-key"

    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            INSERT INTO upstream_keys (
                id, provider_id, key_value, key_masked, status, is_valid, is_usable, priority
            ) VALUES (
                'legacy-key', 'openai', ?, 'sk-p****', 'pending_validation', 0, 0, 0
            )
            """,
            (plaintext,),
        )
        await db.commit()

    converted = await reencrypt_plaintext_upstream_keys(db_path)
    assert converted == 1

    state = await count_storage_encryption_state(db_path)
    assert state["encrypted"] == 1
    assert state["plaintext"] == 0

    fetched = await get_upstream_key(db_path, "legacy-key")
    assert fetched is not None
    assert fetched["key_value"] == plaintext
