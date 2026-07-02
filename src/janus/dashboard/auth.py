from __future__ import annotations

from urllib.parse import quote

from fastapi import Header, HTTPException, Query, Request

from janus.api.auth import authenticate_api_key, extract_api_key, is_trusted_dashboard_client

_LOGIN_PATH = "/dashboard/login"


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or request.method == "GET"


async def require_dashboard_access(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    path = request.url.path
    if path == _LOGIN_PATH or path.startswith(f"{_LOGIN_PATH}/"):
        return
    if is_trusted_dashboard_client(request):
        return
    key = extract_api_key(request, authorization, x_goog_api_key, key_query)
    if await authenticate_api_key(request, key):
        return
    if _wants_html(request):
        next_path = quote(path, safe="/")
        raise HTTPException(
            status_code=303,
            headers={"Location": f"{_LOGIN_PATH}?next={next_path}"},
        )
    raise HTTPException(status_code=401, detail="Dashboard authentication required")
