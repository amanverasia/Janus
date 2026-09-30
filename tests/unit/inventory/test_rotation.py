import pytest

from janus.inventory.key_encryption import (
    ENCRYPTED_PREFIX,
    CredentialEncryptionError,
    credential_is_decryptable,
    decrypt_with_previous_key,
    encrypt_key_value,
    generate_encryption_key,
)
from janus.inventory.rotation import (
    RotationReport,
    assert_credential_encryption_ready,
    count_stored_credentials,
    list_undecryptable_credentials,
    rotate_credentials,
    undecryptable_credential_ids,
)
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider
from janus.storage.upstream_keys import create_upstream_key, get_upstream_key


def _set_keys(
    monkeypatch: pytest.MonkeyPatch,
    current: str | None,
    previous: str | None = None,
) -> None:
    if current is None:
        monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", current)
    if previous is None:
        monkeypatch.delenv("INVENTORY_ENCRYPTION_PREVIOUS_KEY", raising=False)
    else:
        monkeypatch.setenv("INVENTORY_ENCRYPTION_PREVIOUS_KEY", previous)


def test_credential_is_decryptable(monkeypatch: pytest.MonkeyPatch) -> None:
    key = generate_encryption_key()
    _set_keys(monkeypatch, key)
    assert credential_is_decryptable("sk-plaintext")
    assert credential_is_decryptable(encrypt_key_value("sk-secret"))

    other = generate_encryption_key()
    foreign = encrypt_key_value("sk-secret")
    _set_keys(monkeypatch, other)
    assert not credential_is_decryptable(foreign)
    _set_keys(monkeypatch, None)
    assert not credential_is_decryptable(foreign)
    _set_keys(monkeypatch, "not-a-fernet-key")
    assert not credential_is_decryptable(foreign)


def test_decrypt_with_previous_key(monkeypatch: pytest.MonkeyPatch) -> None:
    old_key = generate_encryption_key()
    new_key = generate_encryption_key()
    _set_keys(monkeypatch, old_key)
    stored = encrypt_key_value("sk-rotated")

    _set_keys(monkeypatch, new_key, old_key)
    assert decrypt_with_previous_key(stored) == "sk-rotated"

    _set_keys(monkeypatch, new_key, generate_encryption_key())
    assert decrypt_with_previous_key(stored) is None

    _set_keys(monkeypatch, new_key, None)
    assert decrypt_with_previous_key(stored) is None
    assert decrypt_with_previous_key("sk-plaintext") is None


def test_dev_key_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_keys(monkeypatch, generate_encryption_key())
    assert_credential_encryption_ready(3)

    _set_keys(monkeypatch, None)
    assert_credential_encryption_ready(0)

    with pytest.raises(CredentialEncryptionError, match="JANUS_ALLOW_INSECURE_DEV_KEY=1"):
        assert_credential_encryption_ready(2)

    monkeypatch.setenv("JANUS_ALLOW_INSECURE_DEV_KEY", "1")
    assert_credential_encryption_ready(2)


async def _seed_credentials(db_path) -> tuple[str, str]:
    await create_provider(
        db_path,
        {
            "id": "rotating",
            "prefix": "rotating",
            "api_type": "openai_compat",
            "base_url": "https://rotating.local/v1",
            "api_key": "sk-provider-rotation-secret",
            "models": [],
        },
    )
    key = await create_upstream_key(
        db_path, provider_id="openai", key_value="sk-upstream-rotation-secret"
    )
    return "rotating", str(key["id"])


@pytest.mark.asyncio
async def test_rotation_drill_reseals_previous_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "rotation.db"
    await init_db(db_path)
    old_key = generate_encryption_key()
    new_key = generate_encryption_key()
    _set_keys(monkeypatch, old_key)
    provider_id, key_id = await _seed_credentials(db_path)

    _set_keys(monkeypatch, new_key)
    undecryptable = await list_undecryptable_credentials(db_path)
    assert undecryptable.get("providers") == {provider_id}
    assert undecryptable.get("upstream_keys") == {key_id}
    assert await count_stored_credentials(db_path) == 2

    _set_keys(monkeypatch, new_key, old_key)
    report = await rotate_credentials(db_path)
    assert report.resealed_previous.get("providers") == [provider_id]
    assert report.resealed_previous.get("upstream_keys") == [key_id]
    assert report.total_undecryptable() == 0

    assert await list_undecryptable_credentials(db_path) == {}
    fetched = await get_upstream_key(db_path, key_id)
    assert fetched is not None
    assert fetched["key_value"] == "sk-upstream-rotation-secret"
    assert fetched["credential_decryptable"] is True

    _set_keys(monkeypatch, new_key, None)
    assert await list_undecryptable_credentials(db_path) == {}


@pytest.mark.asyncio
async def test_rotation_without_previous_key_marks_undecryptable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "undecryptable.db"
    await init_db(db_path)
    old_key = generate_encryption_key()
    new_key = generate_encryption_key()
    _set_keys(monkeypatch, old_key)
    provider_id, key_id = await _seed_credentials(db_path)

    _set_keys(monkeypatch, new_key, None)
    report = await rotate_credentials(db_path)
    assert report.resealed_previous == {}
    assert report.total_undecryptable() == 2

    undecryptable = await list_undecryptable_credentials(db_path)
    assert undecryptable.get("providers") == {provider_id}
    assert undecryptable.get("upstream_keys") == {key_id}
    assert await undecryptable_credential_ids(db_path, "upstream_keys", [key_id]) == {key_id}
    assert await undecryptable_credential_ids(db_path, "upstream_keys", ["missing"]) == set()

    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT key_value FROM upstream_keys WHERE id = ?", (key_id,)) as cur:
            row = await cur.fetchone()
    assert isinstance(row[0], str) and row[0].startswith(ENCRYPTED_PREFIX)

    fetched = await get_upstream_key(db_path, key_id)
    assert fetched is not None
    assert fetched["key_value"] is None
    assert fetched["credential_decryptable"] is False


@pytest.mark.asyncio
async def test_rotation_disabled_never_seals_plaintext(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "disabled.db"
    await init_db(db_path)
    _set_keys(monkeypatch, None)
    provider_id, key_id = await _seed_credentials(db_path)

    report = await rotate_credentials(db_path)
    assert report == RotationReport.empty()
    assert await list_undecryptable_credentials(db_path) == {}

    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT api_key FROM providers WHERE id = ?", (provider_id,)) as cur:
            row = await cur.fetchone()
    assert row[0] == "sk-provider-rotation-secret"


@pytest.mark.asyncio
async def test_rotation_seals_plaintext_when_key_enabled(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "seal.db"
    await init_db(db_path)
    _set_keys(monkeypatch, None)
    provider_id, key_id = await _seed_credentials(db_path)

    new_key = generate_encryption_key()
    _set_keys(monkeypatch, new_key)
    report = await rotate_credentials(db_path)
    assert report.sealed_plaintext.get("providers") == [provider_id]
    assert report.sealed_plaintext.get("upstream_keys") == [key_id]
    assert report.total_undecryptable() == 0
    assert await list_undecryptable_credentials(db_path) == {}

    fetched = await get_upstream_key(db_path, key_id)
    assert fetched is not None
    assert fetched["key_value"] == "sk-upstream-rotation-secret"
