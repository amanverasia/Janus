from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.datastructures import MutableHeaders

DASHBOARD_TEST_API_KEY = "sk-dashboard-test"
DASHBOARD_TEST_ANONYMOUS_HEADERS = {"x-test-skip-dashboard-auth": "true"}


def with_dashboard_auth(app: FastAPI) -> FastAPI:
    if DASHBOARD_TEST_API_KEY not in app.state.config.api_keys:
        app.state.config.api_keys.append(DASHBOARD_TEST_API_KEY)

    @app.middleware("http")
    async def inject_dashboard_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        is_login = path == "/dashboard/login" or path.startswith("/dashboard/login/")
        has_explicit_key = bool(
            request.headers.get("authorization")
            or request.headers.get("x-goog-api-key")
            or request.query_params.get("key")
            or request.cookies.get("janus_dashboard_key")
        )
        skip_auth = request.headers.get("x-test-skip-dashboard-auth") == "true"
        if (
            path.startswith("/dashboard")
            and not is_login
            and not has_explicit_key
            and not skip_auth
        ):
            MutableHeaders(scope=request.scope)["authorization"] = (
                f"Bearer {DASHBOARD_TEST_API_KEY}"
            )
        return await call_next(request)

    return app
