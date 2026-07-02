from __future__ import annotations

from fastapi import Header, HTTPException, Query, Request

from janus.api.auth import authenticate_api_key, extract_api_key, is_require_api_key_enabled


async def require_api_key(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    if not await is_require_api_key_enabled(request):
        return

    key = extract_api_key(request, authorization, x_goog_api_key, key_query)
    if await authenticate_api_key(request, key):
        return
    raise HTTPException(status_code=401, detail="Invalid API key")
