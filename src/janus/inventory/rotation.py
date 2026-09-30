from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from janus.inventory.key_encryption import (
    CURRENT_KEY_ENV,
    INSECURE_DEV_KEY_OPT_IN_ENV,
    PREVIOUS_KEY_ENV,
    CredentialEncryptionError,
    credential_is_decryptable,
    decrypt_with_previous_key,
    encrypt_key_value,
    encryption_enabled,
    insecure_dev_key_allowed,
    is_encrypted_value,
)
from janus.storage.database import get_connection

logger = logging.getLogger(__name__)

_CREDENTIAL_SOURCES: tuple[tuple[str, str], ...] = (
    ("providers", "api_key"),
    ("upstream_keys", "key_value"),
)


@dataclass(frozen=True)
class RotationReport:
    resealed_previous: dict[str, list[str]]
    sealed_plaintext: dict[str, list[str]]
    undecryptable: dict[str, list[str]]
    skipped_races: dict[str, list[str]]

    @classmethod
    def empty(cls) -> RotationReport:
        return cls({}, {}, {}, {})

    def total_undecryptable(self) -> int:
        return sum(len(ids) for ids in self.undecryptable.values())

    def log_summary(self, log: logging.Logger) -> None:
        for table, ids in self.resealed_previous.items():
            log.warning(
                "Re-sealed %d %s credential(s) from %s onto the current key: %s",
                len(ids),
                table,
                PREVIOUS_KEY_ENV,
                ", ".join(ids),
            )
        for table, ids in self.sealed_plaintext.items():
            log.warning("Sealed %d plaintext %s credential(s): %s", len(ids), table, ", ".join(ids))
        for table, ids in self.skipped_races.items():
            log.warning(
                "Skipped %d %s credential(s) changed by another writer during rotation: %s",
                len(ids),
                table,
                ", ".join(ids),
            )
        for table, ids in self.undecryptable.items():
            log.error(
                "%d %s credential(s) cannot be decrypted with %s: %s",
                len(ids),
                table,
                CURRENT_KEY_ENV,
                ", ".join(ids),
            )


def assert_credential_encryption_ready(stored_credentials: int) -> None:
    if encryption_enabled() or stored_credentials == 0:
        return
    if insecure_dev_key_allowed():
        logger.warning(
            "%s=1 is set: %d stored credential(s) remain readable only as plaintext "
            "(insecure dev fallback).",
            INSECURE_DEV_KEY_OPT_IN_ENV,
            stored_credentials,
        )
        return
    raise CredentialEncryptionError(
        f"{CURRENT_KEY_ENV} is not set while {stored_credentials} real credential(s) are "
        "stored; Janus refuses to keep real credentials in plaintext. Set "
        f"{CURRENT_KEY_ENV} (generate one with `janus inventory generate-encryption-key`), "
        f"or set {INSECURE_DEV_KEY_OPT_IN_ENV}=1 to explicitly allow insecure plaintext "
        "storage."
    )


def _decryptability(rows: Iterable[Any]) -> set[str]:
    return {
        str(row[0])
        for row in rows
        if isinstance(row[1], str) and not credential_is_decryptable(row[1])
    }


async def count_stored_credentials(db_path: str | Path) -> int:
    total = 0
    async with get_connection(db_path) as db:
        for table, column in _CREDENTIAL_SOURCES:
            async with db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
            ) as cur:
                row = await cur.fetchone()
            total += int(row[0]) if row else 0
    return total


async def list_undecryptable_credentials(db_path: str | Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    async with get_connection(db_path) as db:
        for table, column in _CREDENTIAL_SOURCES:
            async with db.execute(
                f"SELECT id, {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != '' "
                f"AND substr({column}, 1, 7) = 'enc:v1:'"
            ) as cur:
                rows = await cur.fetchall()
            undecryptable = _decryptability(rows)
            if undecryptable:
                result[table] = undecryptable
    return result


async def undecryptable_credential_ids(db_path: str | Path, table: str, ids: list[str]) -> set[str]:
    columns = dict(_CREDENTIAL_SOURCES)
    column = columns.get(table)
    if column is None or not ids:
        return set()
    placeholders = ", ".join("?" for _ in ids)
    async with get_connection(db_path) as db:
        async with db.execute(
            f"SELECT id, {column} FROM {table} WHERE id IN ({placeholders})",
            ids,
        ) as cur:
            rows = await cur.fetchall()
    return _decryptability(rows)


async def _cas_store(
    db: Any, table: str, column: str, row_id: str, expected: str, replacement: str
) -> bool:
    cursor = await db.execute(
        f"UPDATE {table} SET {column} = ?, updated_at = datetime('now') "
        f"WHERE id = ? AND {column} = ?",
        (replacement, row_id, expected),
    )
    return bool(cursor.rowcount)


async def rotate_credentials(db_path: str | Path) -> RotationReport:
    if not encryption_enabled():
        return RotationReport.empty()
    resealed: dict[str, list[str]] = {}
    sealed: dict[str, list[str]] = {}
    undecryptable: dict[str, list[str]] = {}
    races: dict[str, list[str]] = {}
    async with get_connection(db_path) as db:
        for table, column in _CREDENTIAL_SOURCES:
            async with db.execute(
                f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                row_id = str(row[0])
                stored = row[1]
                if not isinstance(stored, str):
                    continue
                if not is_encrypted_value(stored):
                    replacement = encrypt_key_value(stored)
                    if await _cas_store(db, table, column, row_id, stored, replacement):
                        sealed.setdefault(table, []).append(row_id)
                    else:
                        races.setdefault(table, []).append(row_id)
                    continue
                if credential_is_decryptable(stored):
                    continue
                plaintext = decrypt_with_previous_key(stored)
                if plaintext is None:
                    undecryptable.setdefault(table, []).append(row_id)
                    continue
                if await _cas_store(
                    db, table, column, row_id, stored, encrypt_key_value(plaintext)
                ):
                    resealed.setdefault(table, []).append(row_id)
                else:
                    async with db.execute(
                        f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)
                    ) as cur:
                        current = await cur.fetchone()
                    current_value = current[0] if current else None
                    if (
                        isinstance(current_value, str)
                        and current_value != stored
                        and credential_is_decryptable(current_value)
                    ):
                        continue
                    races.setdefault(table, []).append(row_id)
        await db.commit()
    return RotationReport(
        resealed_previous=resealed,
        sealed_plaintext=sealed,
        undecryptable=undecryptable,
        skipped_races=races,
    )
