"""Runtime DB-stored settings (SQLite settings table) — feature toggles, strategies, limits.

These are the live settings read/written via the dashboard, CLI, or YAML seed.
For server-process configuration (port, host, data dir), see ``janus.settings``.
"""

from __future__ import annotations

from pathlib import Path

from .database import get_connection

# Process-level cache of the full settings table, keyed by db path. Settings change
# only via the dashboard/CLI (set_setting) or startup seeding, so we cache reads and
# invalidate on every write. Single-process app: no cross-process coherence needed.
_settings_cache: dict[str, dict[str, str]] = {}


def invalidate_settings_cache(db_path: str | Path | None = None) -> None:
    if db_path is None:
        _settings_cache.clear()
    else:
        _settings_cache.pop(str(db_path), None)


SAVER_SETTING_DEFAULTS: dict[str, str] = {
    "saver_rtk_enabled": "true",
    "saver_caveman_enabled": "false",
    "saver_ponytail_enabled": "false",
    "saver_ponytail_level": "full",
    "saver_headroom_enabled": "false",
    "saver_headroom_url": "http://localhost:8787",
}

SERVER_SETTING_DEFAULTS: dict[str, str] = {
    "server_require_api_key": "true",
    "server_sticky_client_key_routing": "false",
    "server_request_logging": "false",
    "server_account_strategy": "round_robin",
    "server_sticky_limit": "3",
}


async def get_setting(db_path: str | Path, key: str, default: str | None = None) -> str | None:
    settings = await get_all_settings(db_path)
    return settings.get(key, default)


async def set_setting(db_path: str | Path, key: str, value: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()
    invalidate_settings_cache(db_path)


async def get_all_settings(db_path: str | Path) -> dict[str, str]:
    cached = _settings_cache.get(str(db_path))
    if cached is not None:
        return cached
    async with get_connection(db_path) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    result = {row["key"]: row["value"] for row in rows}
    _settings_cache[str(db_path)] = result
    return result


async def ensure_saver_defaults(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        for key, value in SAVER_SETTING_DEFAULTS.items():
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        await db.commit()
    invalidate_settings_cache(db_path)


async def ensure_server_defaults(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        for key, value in SERVER_SETTING_DEFAULTS.items():
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        await db.commit()
    invalidate_settings_cache(db_path)


def resolve_server_settings(settings: dict[str, str]) -> dict[str, str]:
    resolved = dict(SERVER_SETTING_DEFAULTS)
    for key in SERVER_SETTING_DEFAULTS:
        if key in settings:
            resolved[key] = settings[key]
    return resolved


def require_api_key_enabled(settings: dict[str, str]) -> bool:
    return resolve_server_settings(settings)["server_require_api_key"].lower() == "true"


def sticky_client_key_routing_enabled(settings: dict[str, str]) -> bool:
    return resolve_server_settings(settings)["server_sticky_client_key_routing"].lower() == "true"


async def is_sticky_client_key_routing_enabled(db_path: str | Path) -> bool:
    await ensure_server_defaults(db_path)
    settings = await get_all_settings(db_path)
    return sticky_client_key_routing_enabled(settings)


def resolve_account_strategy(settings: dict[str, str]) -> str:
    return resolve_server_settings(settings)["server_account_strategy"]


def resolve_sticky_limit(settings: dict[str, str]) -> int:
    try:
        return int(resolve_server_settings(settings)["server_sticky_limit"])
    except (ValueError, TypeError):
        return 3


def request_logging_enabled(settings: dict[str, str]) -> bool:
    return resolve_server_settings(settings)["server_request_logging"].lower() == "true"


async def is_request_logging_enabled(db_path: str | Path) -> bool:
    settings = await get_all_settings(db_path)
    return request_logging_enabled(settings)


def resolve_saver_settings(settings: dict[str, str]) -> dict[str, str]:
    resolved = dict(SAVER_SETTING_DEFAULTS)
    for key in SAVER_SETTING_DEFAULTS:
        if key in settings:
            resolved[key] = settings[key]
    return resolved


def saver_enabled(settings: dict[str, str], key: str) -> bool:
    return resolve_saver_settings(settings).get(key, "false").lower() == "true"
