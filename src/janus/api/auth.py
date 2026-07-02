from __future__ import annotations

import ipaddress

from fastapi import Request

from janus.storage.settings import get_setting


def extract_api_key(
    request: Request,
    authorization: str = "",
    x_goog_api_key: str = "",
    key_query: str = "",
) -> str | None:
    if authorization.startswith("Bearer "):
        return authorization[7:]
    if x_goog_api_key:
        return x_goog_api_key
    if key_query:
        return key_query
    cookie = request.cookies.get("janus_dashboard_key")
    if cookie:
        return cookie
    return None


async def is_require_api_key_enabled(request: Request) -> bool:
    db_val = await get_setting(request.app.state.db_path, "server_require_api_key")
    if db_val is not None:
        return db_val.lower() == "true"
    return bool(request.app.state.config.server.require_api_key)


async def authenticate_api_key(request: Request, key: str | None) -> bool:
    if not key:
        return False
    if key in request.app.state.config.api_keys:
        return True
    from janus.storage.api_keys import verify_key

    key_id = await verify_key(request.app.state.db_path, key)
    if key_id is not None:
        request.state.client_key_id = key_id
        return True
    return False


def is_trusted_dashboard_client(request: Request) -> bool:
    client = request.client
    if client is None:
        return True
    host = client.host
    if not host or host in {"testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
