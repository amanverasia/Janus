from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
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
    if response.status_code >= 400:
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


class DashboardMutationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            response = await original(request)
            if _should_invalidate(request, response):
                invalidate_dashboard_alerts(request.app)
            return response

        return route_handler
