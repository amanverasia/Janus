from __future__ import annotations

from pathlib import Path

from .database import get_connection

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
}


async def get_setting(db_path: str | Path, key: str, default: str | None = None) -> str | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(db_path: str | Path, key: str, value: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_all_settings(db_path: str | Path) -> dict[str, str]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    return {row["key"]: row["value"] for row in rows}


async def ensure_saver_defaults(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        for key, value in SAVER_SETTING_DEFAULTS.items():
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        await db.commit()


async def ensure_server_defaults(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        for key, value in SERVER_SETTING_DEFAULTS.items():
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        await db.commit()


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
