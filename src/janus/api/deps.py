from __future__ import annotations

from fastapi import Header, HTTPException, Request


async def require_api_key(request: Request, authorization: str = Header(default="")) -> None:
    config = request.app.state.config
    if not config.server.require_api_key:
        return
    if not config.api_keys:
        return
    if authorization.startswith("Bearer "):
        key = authorization[7:]
        if key in config.api_keys:
            return
    raise HTTPException(status_code=401, detail="Invalid API key")
