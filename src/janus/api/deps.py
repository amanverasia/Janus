from __future__ import annotations

from fastapi import Header, HTTPException, Request


async def require_api_key(request: Request, authorization: str = Header(default="")) -> None:
    config = request.app.state.config
    if not config.server.require_api_key:
        return
    if authorization.startswith("Bearer "):
        key = authorization[7:]
        if key in config.api_keys:
            return
        from janus.storage.api_keys import verify_key

        db_path = request.app.state.db_path
        if await verify_key(db_path, key):
            return
    raise HTTPException(status_code=401, detail="Invalid API key")
