from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from janus.dashboard.alerts import invalidate_dashboard_alerts

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_ONLY_POST_PATHS = frozenset(
    {
        "/dashboard/api/oauth/copilot/poll",
        "/dashboard/api/oauth/copilot/start",
        "/dashboard/api/providers/fetch-models",
    }
)


def _should_invalidate(request: Request, response: Response) -> bool:
    path = request.url.path
    if request.method not in _MUTATION_METHODS or not path.startswith("/dashboard/api/"):
        return False
    if response.status_code >= 400 and response.status_code != 422:
        return False
    if request.method == "POST" and path in _READ_ONLY_POST_PATHS:
        return False
    if request.method == "POST" and path.startswith("/dashboard/api/providers/"):
        return not path.endswith("/test")
    if request.method == "POST" and path.startswith("/dashboard/api/inventory/keys/"):
        return not path.endswith("/reveal")
    if request.method == "POST" and path == "/dashboard/api/inventory/reclassify":
        dry = request.query_params.get("dry", "true").casefold()
        return dry not in {"1", "on", "t", "true", "y", "yes"}
    return True


def _is_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        origin_parts = urlsplit(origin)
        request_parts = urlsplit(str(request.base_url))
    except ValueError:
        return False
    return (
        origin_parts.scheme == request_parts.scheme
        and origin_parts.netloc.lower() == request_parts.netloc.lower()
    )


class DashboardMutationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            if (
                request.method in _MUTATION_METHODS
                and request.url.path.startswith("/dashboard/")
                and not _is_same_origin(request)
            ):
                return JSONResponse(
                    {"error": "Cross-origin dashboard request rejected"}, status_code=403
                )
            response = await original(request)
            if _should_invalidate(request, response):
                invalidate_dashboard_alerts(request.app)
            return response

        return route_handler
