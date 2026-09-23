#!/usr/bin/env python3
"""Representative SQLite migration smoke for CI (#122).

Builds a database with a deliberately pre-migration schema (providers and
api_keys tables missing every column added by later releases), runs
``init_db`` on it, and asserts that:

1. every migrated column now exists,
2. pre-existing rows survive migration,
3. legacy dashboard credential settings are purged,
4. a second ``init_db`` run changes nothing (idempotency).

Exit code 0 means the upgrade path is intact.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

LEGACY_SCHEMA = """
CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT,
    api_key TEXT,
    models TEXT
);

CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER,
    daily_limit REAL NOT NULL,
    warn_pct REAL DEFAULT 80,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (key_id) REFERENCES api_keys(id)
);

INSERT INTO budgets (id, daily_limit, warn_pct) VALUES (7, 10, 75);

INSERT INTO providers (id, prefix, api_type, base_url, api_key, models)
VALUES ('legacy-openai', 'openai', 'openai_compat', 'https://api.example/v1', 'sk-legacy',
        '["gpt-test"]');
"""

NEW_PROVIDER_COLUMNS = {
    "catalog_id",
    "default_model",
    "live_models",
    "selected_models",
    "quota_window",
    "quota_limit",
    "quota_metric",
    "transports",
    "allowed_models",
}
NEW_API_KEY_COLUMNS = {"can_login", "allowed_models"}


def _table_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {str(row[1]) for row in rows}


def _schema_snapshot(db_path: Path) -> dict[str, tuple[tuple[str, ...], ...]]:
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: tuple(
                conn.execute(f"PRAGMA table_info({table})").fetchall(),
            )
            for table in tables
        }
    finally:
        conn.close()


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="janus-migration-smoke-"))
    db_path = tmp / "janus.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    from janus.storage.database import init_db

    await init_db(db_path)

    provider_columns = _table_columns(db_path, "providers")
    missing_providers = NEW_PROVIDER_COLUMNS - provider_columns
    assert not missing_providers, f"providers migration missed columns: {sorted(missing_providers)}"

    key_columns = _table_columns(db_path, "api_keys")
    missing_keys = NEW_API_KEY_COLUMNS - key_columns
    assert not missing_keys, f"api_keys migration missed columns: {sorted(missing_keys)}"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT prefix, api_type FROM providers WHERE id = 'legacy-openai'"
        ).fetchone()
        assert row == ("openai", "openai_compat"), f"legacy provider row damaged: {row}"
        budget = conn.execute(
            "SELECT id, daily_limit, absolute_limit, warn_pct FROM budgets"
        ).fetchone()
        assert budget == (7, 10.0, None, 75.0), f"legacy budget row damaged: {budget}"
        budget_columns = conn.execute("PRAGMA table_info(budgets)").fetchall()
        assert next(col for col in budget_columns if col[1] == "daily_limit")[3] == 0
        legacy_settings = [
            "dashboard_username",
            "dashboard_password_hash",
            "dashboard_session_secret",
        ]
        for key in legacy_settings:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, "must-be-purged"))
        conn.commit()
    finally:
        conn.close()

    await init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        survivors = conn.execute(
            "SELECT key FROM settings WHERE key IN (?, ?, ?)", tuple(legacy_settings)
        ).fetchall()
        assert not survivors, f"legacy dashboard settings survived: {survivors}"
    finally:
        conn.close()

    before = _schema_snapshot(db_path)
    await init_db(db_path)
    assert _schema_snapshot(db_path) == before, "init_db is not idempotent"

    print(
        "migration smoke ok: "
        f"providers +{len(NEW_PROVIDER_COLUMNS)} api_keys +{len(NEW_API_KEY_COLUMNS)} columns, "
        "rows preserved, legacy settings purged, idempotent"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
