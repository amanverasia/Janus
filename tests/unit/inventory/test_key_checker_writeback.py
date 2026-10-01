from __future__ import annotations

from typing import Any

from janus.inventory.key_checker import check_upstream_key
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider, get_provider
from janus.storage.upstream_keys import (
    create_upstream_key,
    get_upstream_key,
    update_upstream_key,
)

OLD = '{"access_token":"at-old","refresh_token":"rt-old"}'
NEW = '{"access_token":"at-new","refresh_token":"rt-new"}'
OTHER = '{"access_token":"at-other","refresh_token":"rt-other"}'


async def _mirrored_key(tmp_path: Any) -> tuple[Any, str]:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
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
    await update_upstream_key(db_path, str(key["id"]), {"status": "active", "is_valid": 1})
    return db_path, str(key["id"])


def _validator(result: dict[str, Any]) -> Any:
    async def fake_validate(
        key_value: str, provider_id: str, metadata: dict[str, Any] | None
    ) -> dict[str, Any]:
        return dict(result)

    return fake_validate


async def test_valid_rotation_updates_upstream_key_and_provider_mirror(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_path, key_id = await _mirrored_key(tmp_path)
    monkeypatch.setattr(
        "janus.inventory.key_checker.validate_key",
        _validator({"is_valid": True, "is_usable": True, "key_value": NEW}),
    )
    monkeypatch.setattr("janus.inventory.key_checker.ACCOUNT_VALUE_PROBES", {})

    await check_upstream_key(db_path, key_id)

    row = await get_upstream_key(db_path, key_id)
    provider = await get_provider(db_path, "cl")
    assert row is not None and row["key_value"] == NEW
    assert row["status"] == "active"
    assert provider is not None and provider["api_key"] == NEW


async def test_inconclusive_rotation_updates_provider_mirror(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_path, key_id = await _mirrored_key(tmp_path)
    monkeypatch.setattr(
        "janus.inventory.key_checker.validate_key",
        _validator({"probe_inconclusive": True, "error": "HTTP 429", "key_value": NEW}),
    )

    await check_upstream_key(db_path, key_id)

    row = await get_upstream_key(db_path, key_id)
    provider = await get_provider(db_path, "cl")
    assert row is not None and row["key_value"] == NEW
    assert row["status"] == "active"
    assert provider is not None and provider["api_key"] == NEW


async def test_invalid_result_after_refresh_persists_rotated_blob(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_path, key_id = await _mirrored_key(tmp_path)
    monkeypatch.setattr(
        "janus.inventory.key_checker.validate_key",
        _validator({"is_valid": False, "error": "rejected (401)", "key_value": NEW}),
    )

    await check_upstream_key(db_path, key_id)

    row = await get_upstream_key(db_path, key_id)
    provider = await get_provider(db_path, "cl")
    assert row is not None and row["key_value"] == NEW
    assert row["status"] == "invalid"
    assert provider is not None and provider["api_key"] == NEW


async def test_rotation_lost_to_concurrent_writer_is_not_overwritten(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_path, key_id = await _mirrored_key(tmp_path)

    async def fake_validate(
        key_value: str, provider_id: str, metadata: dict[str, Any] | None
    ) -> dict[str, Any]:
        await update_upstream_key(db_path, key_id, {"key_value": OTHER})
        return {"is_valid": True, "is_usable": True, "key_value": NEW}

    monkeypatch.setattr("janus.inventory.key_checker.validate_key", fake_validate)
    monkeypatch.setattr("janus.inventory.key_checker.ACCOUNT_VALUE_PROBES", {})

    await check_upstream_key(db_path, key_id)

    row = await get_upstream_key(db_path, key_id)
    assert row is not None and row["key_value"] == OTHER


async def test_invalid_result_after_concurrent_rotation_is_inconclusive(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_path, key_id = await _mirrored_key(tmp_path)

    async def fake_validate(
        key_value: str, provider_id: str, metadata: dict[str, Any] | None
    ) -> dict[str, Any]:
        await update_upstream_key(db_path, key_id, {"key_value": NEW})
        return {"is_valid": False, "error": "Claude OAuth refresh failed; re-export"}

    monkeypatch.setattr("janus.inventory.key_checker.validate_key", fake_validate)

    await check_upstream_key(db_path, key_id)

    row = await get_upstream_key(db_path, key_id)
    assert row is not None
    assert row["key_value"] == NEW
    assert row["status"] == "active"
    assert row["is_valid"] == 1
    assert int(row["consecutive_failures"] or 0) == 0
    assert row["last_error"] == "credential changed during validation"
