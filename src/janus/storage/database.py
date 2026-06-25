from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from janus.config.schema import JanusConfig

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    provider_id TEXT,
    model TEXT,
    account_id TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    status INTEGER
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER,
    daily_limit REAL NOT NULL,
    warn_pct REAL DEFAULT 80,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (key_id) REFERENCES api_keys(id)
);

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT,
    models TEXT NOT NULL DEFAULT '[]',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    models TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_overrides (
    model TEXT PRIMARY KEY,
    input_per_mtok REAL NOT NULL,
    output_per_mtok REAL NOT NULL,
    cache_creation_per_mtok REAL NOT NULL DEFAULT 0.0,
    cache_read_per_mtok REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider_id);
"""

_NEW_USAGE_COLUMNS = [
    ("cost", "REAL DEFAULT 0.0"),
    ("cache_creation_tokens", "INTEGER DEFAULT 0"),
    ("cache_read_tokens", "INTEGER DEFAULT 0"),
    ("client_key_id", "INTEGER"),
]


async def _migrate_usage_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(usage)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    for col, col_type in _NEW_USAGE_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE usage ADD COLUMN {col} {col_type}")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_cost_key ON usage(client_key_id, date(timestamp))"
    )


async def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA)
        await _migrate_usage_columns(db)
        await db.commit()


@asynccontextmanager
async def get_connection(db_path: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def _table_is_empty(db: aiosqlite.Connection, table: str) -> bool:
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        row = await cur.fetchone()
    return row is None or row[0] == 0


async def seed_from_config(db_path: str | Path, config: JanusConfig) -> None:
    async with get_connection(db_path) as db:
        if await _table_is_empty(db, "providers") and config.providers:
            for pc in config.providers:
                await db.execute(
                    """INSERT INTO providers (id, prefix, api_type, base_url, api_key, models)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (pc.id, pc.prefix, pc.api_type, pc.base_url, pc.api_key, json.dumps(pc.models)),
                )

        if await _table_is_empty(db, "combos") and config.combos:
            for combo in config.combos:
                await db.execute(
                    "INSERT INTO combos (name, models) VALUES (?, ?)",
                    (combo.name, json.dumps(combo.models)),
                )

        if await _table_is_empty(db, "settings"):
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                ("saver_rtk_enabled", "true" if config.token_savers.rtk.enabled else "false"),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (
                    "saver_caveman_enabled",
                    "true" if config.token_savers.caveman.enabled else "false",
                ),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (
                    "saver_ponytail_enabled",
                    "true" if config.token_savers.ponytail.enabled else "false",
                ),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                ("saver_ponytail_level", config.token_savers.ponytail.level),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                ("server_require_api_key", "true" if config.server.require_api_key else "false"),
            )

        if await _table_is_empty(db, "pricing_overrides") and config.pricing:
            for model, rates in config.pricing.items():
                await db.execute(
                    """INSERT INTO pricing_overrides
                       (model, input_per_mtok, output_per_mtok,
                        cache_creation_per_mtok, cache_read_per_mtok)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        model,
                        rates.get("input_per_mtok", 0.0),
                        rates.get("output_per_mtok", 0.0),
                        rates.get("cache_creation_per_mtok", 0.0),
                        rates.get("cache_read_per_mtok", 0.0),
                    ),
                )

        await db.commit()
