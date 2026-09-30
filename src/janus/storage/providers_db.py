from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from janus.inventory.key_encryption import (
    CredentialDecryptionError,
    decrypt_key_value,
    encrypt_key_value,
    encryption_enabled,
    is_encrypted_value,
)

from .database import get_connection

_PROVIDER_PUBLIC_COLUMNS = (
    "id",
    "catalog_id",
    "prefix",
    "api_type",
    "base_url",
    "models",
    "default_model",
    "live_models",
    "selected_models",
    "is_enabled",
    "created_at",
    "updated_at",
    "quota_window",
    "quota_limit",
    "quota_metric",
    "transports",
    "allowed_models",
)


def _provider_projection(include_secret: bool) -> str:
    if include_secret:
        return "*"
    return ", ".join(_PROVIDER_PUBLIC_COLUMNS) + (
        ", CASE WHEN api_key IS NULL THEN NULL WHEN api_key = '' THEN '' ELSE '*' END AS api_key"
    )


def _stored_api_key(value: Any) -> Any:
    if isinstance(value, str) and value:
        if is_encrypted_value(value):
            return value
        return encrypt_key_value(value)
    return value


def _json_text(value: Any, *, default: list[Any] | dict[str, Any]) -> str:
    if value is None:
        return json.dumps(default)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return json.dumps(default)
        if isinstance(default, list) and isinstance(parsed, list):
            return json.dumps(parsed)
        if isinstance(default, dict) and isinstance(parsed, dict):
            return json.dumps(parsed)
        return json.dumps(default)
    if isinstance(default, list) and not isinstance(value, list):
        return json.dumps(default)
    if isinstance(default, dict) and not isinstance(value, dict):
        return json.dumps(default)
    return json.dumps(value)


def _bool_int(value: Any, *, default: bool) -> int:
    if value is None:
        return int(default)
    if isinstance(value, str):
        return int(value.strip().lower() not in {"", "0", "false", "no", "off"})
    return int(bool(value))


def _decode_provider_row(row: Any, *, include_secret: bool = True) -> dict[str, Any]:
    item = dict(row)
    api_key = item.get("api_key")
    if include_secret and isinstance(api_key, str) and api_key:
        try:
            item["api_key"] = decrypt_key_value(api_key)
            item["credential_decryptable"] = True
        except CredentialDecryptionError:
            item["api_key"] = None
            item["credential_decryptable"] = False
    return item


async def list_providers(
    db_path: str | Path, enabled_only: bool = False, *, include_secret: bool = False
) -> list[dict[str, Any]]:
    query = f"SELECT {_provider_projection(include_secret)} FROM providers"
    if enabled_only:
        query += " WHERE is_enabled = 1"
    query += " ORDER BY id"
    async with get_connection(db_path) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
    return [_decode_provider_row(row, include_secret=include_secret) for row in rows]


async def get_provider(
    db_path: str | Path, provider_id: str, *, include_secret: bool = True
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            f"SELECT {_provider_projection(include_secret)} FROM providers WHERE id = ?",
            (provider_id,),
        ) as cur:
            row = await cur.fetchone()
    return _decode_provider_row(row, include_secret=include_secret) if row else None


async def create_provider(db_path: str | Path, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO providers
               (id, catalog_id, prefix, api_type, base_url, api_key, models,
                default_model, live_models, selected_models, quota_window, quota_limit,
                quota_metric, transports, allowed_models)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data.get("catalog_id"),
                data["prefix"],
                data["api_type"],
                data["base_url"],
                _stored_api_key(data.get("api_key")),
                _json_text(data.get("models"), default=[]),
                data.get("default_model"),
                _bool_int(data.get("live_models"), default=True),
                _json_text(data.get("selected_models"), default=[]),
                data.get("quota_window"),
                data.get("quota_limit"),
                data.get("quota_metric") or "requests",
                _json_text(data.get("transports"), default={}) if data.get("transports") else None,
                _json_text(data.get("allowed_models"), default=[]),
            ),
        )
        await db.commit()


async def update_provider(db_path: str | Path, provider_id: str, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM providers WHERE id = ?",
            (provider_id,),
        ) as cur:
            existing = await cur.fetchone()
        if existing is None:
            return
        current = dict(existing)

        def field(name: str) -> Any:
            return data[name] if name in data else current.get(name)

        previous_prefix = str(current["prefix"])
        next_prefix = str(field("prefix"))
        await db.execute(
            """UPDATE providers SET catalog_id = ?, prefix = ?, api_type = ?, base_url = ?,
               api_key = ?, models = ?, default_model = ?, live_models = ?,
               selected_models = ?, quota_window = ?, quota_limit = ?, quota_metric = ?,
               transports = ?, allowed_models = ?,
               updated_at = datetime('now')
               WHERE id = ?""",
            (
                field("catalog_id"),
                field("prefix"),
                field("api_type"),
                field("base_url"),
                _stored_api_key(field("api_key")),
                _json_text(field("models"), default=[]),
                field("default_model"),
                _bool_int(field("live_models"), default=True),
                _json_text(field("selected_models"), default=[]),
                field("quota_window"),
                field("quota_limit"),
                field("quota_metric") or "requests",
                _json_text(field("transports"), default={}) if field("transports") else None,
                _json_text(field("allowed_models"), default=[]),
                provider_id,
            ),
        )
        if next_prefix != previous_prefix:
            async with db.execute(
                "SELECT id FROM providers WHERE prefix = ? AND id != ? ORDER BY id LIMIT 1",
                (previous_prefix, provider_id),
            ) as cursor:
                replacement = await cursor.fetchone()
            if replacement is not None:
                await db.execute(
                    "UPDATE custom_models SET provider_id = ? WHERE provider_id = ?",
                    (replacement["id"], provider_id),
                )
            else:
                await db.execute(
                    "UPDATE custom_models SET provider_prefix = ? WHERE provider_id = ?",
                    (next_prefix, provider_id),
                )
        await db.commit()


async def reencrypt_plaintext_provider_keys(db_path: str | Path) -> int:
    if not encryption_enabled():
        raise RuntimeError("INVENTORY_ENCRYPTION_KEY must be set to encrypt provider credentials")
    converted = 0
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, api_key FROM providers WHERE api_key IS NOT NULL AND api_key != ''"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            stored = row["api_key"]
            if not isinstance(stored, str) or is_encrypted_value(stored):
                continue
            await db.execute(
                "UPDATE providers SET api_key = ?, updated_at = datetime('now') WHERE id = ?",
                (encrypt_key_value(stored), row["id"]),
            )
            converted += 1
        await db.commit()
    return converted


async def count_provider_encryption_state(db_path: str | Path) -> dict[str, int]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(substr(api_key, 1, 7) = 'enc:v1:'), 0) AS encrypted "
            "FROM providers WHERE api_key IS NOT NULL AND api_key != ''"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    total = int(row["total"])
    encrypted = int(row["encrypted"])
    return {"encrypted": encrypted, "plaintext": total - encrypted, "total": total}


async def toggle_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE providers SET is_enabled = 1 - is_enabled,"
            " updated_at = datetime('now') WHERE id = ?",
            (provider_id,),
        )
        await db.commit()


async def delete_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT prefix FROM providers WHERE id = ?", (provider_id,)
        ) as cursor:
            provider = await cursor.fetchone()
        if provider is None:
            return
        async with db.execute(
            "SELECT id FROM providers WHERE prefix = ? AND id != ? ORDER BY id LIMIT 1",
            (provider["prefix"], provider_id),
        ) as cursor:
            replacement = await cursor.fetchone()
        if replacement is not None:
            await db.execute(
                "UPDATE custom_models SET provider_id = ? WHERE provider_id = ?",
                (replacement["id"], provider_id),
            )
        await db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        await db.commit()
