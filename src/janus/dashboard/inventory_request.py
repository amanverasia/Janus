from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from janus.dashboard.mutation_route import DashboardMutationRoute

MAX_INVENTORY_BODY_BYTES = 4 * 1024 * 1024
MAX_INVENTORY_IMPORT_BYTES = 16 * 1024 * 1024


class InventoryMutationRoute(DashboardMutationRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            if request.method in {"POST", "PUT", "PATCH"}:
                limit = (
                    MAX_INVENTORY_IMPORT_BYTES
                    if request.url.path.endswith("/import")
                    else MAX_INVENTORY_BODY_BYTES
                )
                too_large = JSONResponse(
                    {"ok": False, "error": "Inventory request body is too large."},
                    status_code=413,
                    headers={"Cache-Control": "no-store"},
                )
                try:
                    declared = int(request.headers.get("content-length") or 0)
                except ValueError:
                    return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
                if declared > limit:
                    return too_large
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > limit:
                        return too_large
                    body.extend(chunk)
                request._body = bytes(body)
            return await original(request)

        return route_handler
