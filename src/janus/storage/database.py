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

CREATE TABLE IF NOT EXISTS cooldowns (
    account_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '__all__',
    expires_at REAL NOT NULL,
    error_type TEXT,
    backoff_level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, model)
);

CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider_id);

CREATE TABLE IF NOT EXISTS inventory_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'api_key',
    auth_header TEXT NOT NULL DEFAULT 'Authorization',
    auth_prefix TEXT NOT NULL DEFAULT 'Bearer',
    key_env_var TEXT,
    models_endpoint TEXT,
    health_check_endpoint TEXT,
    credit_check_endpoint TEXT,
    billing_model TEXT NOT NULL DEFAULT 'unknown',
    is_direct INTEGER NOT NULL DEFAULT 1,
    routing_note TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upstream_keys (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES inventory_providers(id),
    key_label TEXT,
    key_value TEXT NOT NULL,
    key_hash TEXT,
    key_masked TEXT NOT NULL,
    custom_base_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending_validation',
    is_valid INTEGER NOT NULL DEFAULT 0,
    health_status TEXT DEFAULT 'healthy',
    health_warnings TEXT,
    is_usable INTEGER NOT NULL DEFAULT 0,
    usability_status TEXT DEFAULT 'unknown',
    usability_note TEXT,
    credits_remaining REAL,
    credits_total REAL,
    credits_used REAL,
    rate_limit_rpm INTEGER,
    rate_limit_tpm INTEGER,
    rate_limit_rpd INTEGER,
    usage_current_rpm INTEGER DEFAULT 0,
    usage_current_tpm INTEGER DEFAULT 0,
    daily_credit_limit REAL,
    daily_credit_used REAL DEFAULT 0,
    daily_credit_date TEXT,
    is_daily_limited INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    source_node TEXT,
    last_checked_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upstream_models (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES inventory_providers(id),
    upstream_key_id TEXT REFERENCES upstream_keys(id),
    model_id TEXT NOT NULL,
    display_name TEXT,
    context_window INTEGER,
    max_output_tokens INTEGER,
    pricing_input REAL,
    pricing_output REAL,
    pricing_cached_input REAL,
    capabilities TEXT,
    benchmarks TEXT,
    is_available INTEGER NOT NULL DEFAULT 1,
    tokens_per_second REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upstream_key_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upstream_key_id TEXT NOT NULL REFERENCES upstream_keys(id),
    previous_status TEXT,
    new_status TEXT NOT NULL,
    credits_remaining REAL,
    notes TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_upstream_keys_provider ON upstream_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_upstream_keys_status ON upstream_keys(status);
CREATE INDEX IF NOT EXISTS idx_upstream_models_provider ON upstream_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_upstream_models_key ON upstream_models(upstream_key_id);
CREATE INDEX IF NOT EXISTS idx_upstream_key_history_key ON upstream_key_history(upstream_key_id);
CREATE INDEX IF NOT EXISTS idx_upstream_keys_key_hash ON upstream_keys(key_hash);

CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    client_format TEXT,
    model TEXT,
    provider_id TEXT,
    account_id TEXT,
    status INTEGER,
    duration_ms INTEGER,
    streamed INTEGER NOT NULL DEFAULT 0,
    request_body TEXT,
    response_body TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(timestamp);
"""

_UPSTREAM_KEY_NEW_COLUMNS = [
    ("key_hash", "TEXT"),
]

_NEW_USAGE_COLUMNS = [
    ("cost", "REAL DEFAULT 0.0"),
    ("cache_creation_tokens", "INTEGER DEFAULT 0"),
    ("cache_read_tokens", "INTEGER DEFAULT 0"),
    ("client_key_id", "INTEGER"),
    ("client_key_label", "TEXT"),
]

_NEW_PROVIDER_COLUMNS = [
    ("quota_window", "TEXT"),
    ("quota_limit", "INTEGER"),
    ("quota_metric", "TEXT DEFAULT 'requests'"),
    ("transports", "TEXT"),
]


async def _migrate_provider_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(providers)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    for col, col_type in _NEW_PROVIDER_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE providers ADD COLUMN {col} {col_type}")


async def _migrate_upstream_key_columns(db: aiosqlite.Connection) -> None:
    from janus.inventory.key_encryption import (
        decrypt_key_value,
        hash_upstream_key,
        is_encrypted_value,
    )

    cursor = await db.execute("PRAGMA table_info(upstream_keys)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    for col, col_type in _UPSTREAM_KEY_NEW_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE upstream_keys ADD COLUMN {col} {col_type}")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_upstream_keys_key_hash ON upstream_keys(key_hash)"
    )
    async with db.execute("SELECT id, key_value, key_hash FROM upstream_keys") as cur:
        key_rows = await cur.fetchall()
    for row in key_rows:
        row_id, key_value, key_hash = row[0], row[1], row[2]
        if key_hash:
            continue
        stored = key_value
        if not isinstance(stored, str):
            continue
        plaintext = decrypt_key_value(stored) if is_encrypted_value(stored) else stored
        await db.execute(
            "UPDATE upstream_keys SET key_hash = ? WHERE id = ?",
            (hash_upstream_key(plaintext), row_id),
        )


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


async def _migrate_cooldowns_per_model(db: aiosqlite.Connection) -> None:
    # Rebuild the cooldowns table with a compound (account_id, model) PK.
    # The whole sequence runs inside init_db's single uncommitted transaction
    # (SQLite has transactional DDL), so a crash before commit rolls back with
    # the original table intact. The leading DROP IF EXISTS also makes a retry
    # safe if a prior partial run somehow left cooldowns_new behind.
    cursor = await db.execute("PRAGMA table_info(cooldowns)")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if "model" in existing:
        return
    await db.execute("DROP TABLE IF EXISTS cooldowns_new")
    await db.execute(
        """CREATE TABLE cooldowns_new (
            account_id TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '__all__',
            expires_at REAL NOT NULL,
            error_type TEXT,
            backoff_level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, model)
        )"""
    )
    await db.execute(
        "INSERT INTO cooldowns_new (account_id, model, expires_at) "
        "SELECT account_id, '__all__', expires_at FROM cooldowns"
    )
    await db.execute("DROP TABLE cooldowns")
    await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")


async def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA)
        await _migrate_usage_columns(db)
        await _migrate_provider_columns(db)
        await _migrate_upstream_key_columns(db)
        await _migrate_cooldowns_per_model(db)
        await db.commit()
    await seed_inventory_providers(db_path)


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
                    """INSERT INTO providers
                       (id, prefix, api_type, base_url, api_key, models, transports)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pc.id,
                        pc.prefix,
                        pc.api_type,
                        pc.base_url,
                        pc.api_key,
                        json.dumps(pc.models),
                        json.dumps(pc.transports) if pc.transports else None,
                    ),
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
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                ("server_account_strategy", config.server.account_strategy),
            )
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                ("server_sticky_limit", str(config.server.sticky_limit)),
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

    from janus.storage.settings import invalidate_settings_cache

    invalidate_settings_cache(db_path)


async def seed_inventory_providers(db_path: str | Path) -> None:
    from janus.inventory.catalog import INVENTORY_PROVIDERS

    async with get_connection(db_path) as db:
        for provider in INVENTORY_PROVIDERS.values():
            await db.execute(
                """INSERT INTO inventory_providers
                   (id, name, display_name, base_url, auth_type, auth_header, auth_prefix,
                    key_env_var, models_endpoint, health_check_endpoint, credit_check_endpoint,
                    billing_model, is_direct, routing_note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name,
                     display_name = excluded.display_name,
                     base_url = excluded.base_url,
                     auth_type = excluded.auth_type,
                     auth_header = excluded.auth_header,
                     auth_prefix = excluded.auth_prefix,
                     key_env_var = excluded.key_env_var,
                     models_endpoint = excluded.models_endpoint,
                     health_check_endpoint = excluded.health_check_endpoint,
                     credit_check_endpoint = excluded.credit_check_endpoint,
                     billing_model = excluded.billing_model,
                     is_direct = excluded.is_direct,
                     routing_note = excluded.routing_note,
                     updated_at = datetime('now')""",
                (
                    provider["id"],
                    provider["name"],
                    provider["display_name"],
                    provider["base_url"],
                    provider["auth_type"],
                    provider["auth_header"],
                    provider["auth_prefix"],
                    provider.get("key_env_var"),
                    provider.get("models_endpoint"),
                    provider.get("health_check_endpoint"),
                    provider.get("credit_check_endpoint"),
                    provider.get("billing_model", "unknown"),
                    1 if provider.get("is_direct", True) else 0,
                    provider.get("routing_note"),
                ),
            )
        await db.commit()
