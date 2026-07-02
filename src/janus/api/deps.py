from __future__ import annotations

from fastapi import Header, HTTPException, Query, Request


async def require_api_key(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    config = request.app.state.config
    if not config.server.require_api_key:
        return

    key: str | None = None
    if authorization.startswith("Bearer "):
        key = authorization[7:]
    elif x_goog_api_key:
        key = x_goog_api_key
    elif key_query:
        key = key_query

    if key:
        if key in config.api_keys:
            return
        from janus.storage.api_keys import verify_key

        db_path = request.app.state.db_path
        key_id = await verify_key(db_path, key)
        if key_id is not None:
            request.state.client_key_id = key_id
            return
    raise HTTPException(status_code=401, detail="Invalid API key")
