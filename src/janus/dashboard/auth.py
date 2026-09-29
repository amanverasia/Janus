from __future__ import annotations

from urllib.parse import quote

from fastapi import Header, HTTPException, Query, Request

from janus.api.auth import (
    authenticate_api_key,
    extract_api_key,
    key_can_login,
)

_LOGIN_PATH = "/dashboard/login"


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


async def _authorize(
    request: Request,
    authorization: str,
    x_goog_api_key: str,
    key_query: str,
    *,
    allow_query_key: bool,
) -> None:
    path = request.url.path
    if path == _LOGIN_PATH:
        return
    key = extract_api_key(
        request,
        authorization,
        x_goog_api_key,
        key_query if allow_query_key else "",
    )
    if await authenticate_api_key(request, key) and key_can_login(request):
        return
    if _wants_html(request):
        next_path = quote(path, safe="/")
        raise HTTPException(
            status_code=303,
            headers={"Location": f"{_LOGIN_PATH}?next={next_path}"},
        )
    raise HTTPException(status_code=401, detail="Dashboard authentication required")


async def require_dashboard_access(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    await _authorize(request, authorization, x_goog_api_key, key_query, allow_query_key=True)


async def require_dashboard_access_no_query_key(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    await _authorize(request, authorization, x_goog_api_key, key_query, allow_query_key=False)
