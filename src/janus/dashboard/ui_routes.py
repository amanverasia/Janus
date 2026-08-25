from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from janus.dashboard.auth import require_dashboard_access

router = APIRouter(dependencies=[Depends(require_dashboard_access)])

_APP_INDEX = Path(__file__).parent / "static" / "app" / "index.html"


def _app_response() -> FileResponse:
    if not _APP_INDEX.is_file():
        raise HTTPException(status_code=503, detail="Dashboard application is not built")
    return FileResponse(
        _APP_INDEX,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui", include_in_schema=False)
async def dashboard_ui() -> FileResponse:
    return _app_response()


@router.get("/ui/{path:path}", include_in_schema=False)
async def dashboard_ui_fallback(path: str) -> FileResponse:
    return _app_response()
