from __future__ import annotations

from fastapi import Header, HTTPException, Query, Request

from janus.api.auth import authenticate_api_key, extract_api_key, is_require_api_key_enabled


async def require_api_key(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    key = extract_api_key(request, authorization, x_goog_api_key, key_query)
    if key:
        await authenticate_api_key(request, key)

    if not await is_require_api_key_enabled(request):
        return

    if not key or not (
        getattr(request.state, "client_key_id", None) is not None
        or getattr(request.state, "client_key_label", None) is not None
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")
