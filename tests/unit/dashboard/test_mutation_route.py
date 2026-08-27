from fastapi import Request, Response
from fastapi.routing import APIRoute

from janus.dashboard.mutation_route import DashboardMutationRoute, _should_invalidate


def _request(method: str, path: str, query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
    )


def test_successful_dashboard_mutation_invalidates() -> None:
    assert _should_invalidate(_request("POST", "/dashboard/api/budgets"), Response(status_code=200))


def test_failed_dashboard_mutation_does_not_invalidate() -> None:
    assert not _should_invalidate(
        _request("POST", "/dashboard/api/budgets"), Response(status_code=422)
    )


def test_read_only_dashboard_posts_do_not_invalidate() -> None:
    paths = (
        "/dashboard/api/providers/fetch-models",
        "/dashboard/api/oauth/copilot/start",
        "/dashboard/api/oauth/copilot/poll",
        "/dashboard/api/providers/provider-id/test",
        "/dashboard/api/inventory/keys/key-id/reveal",
    )

    for path in paths:
        assert not _should_invalidate(_request("POST", path), Response(status_code=200))


def test_inventory_validation_post_invalidates() -> None:
    assert _should_invalidate(
        _request("POST", "/dashboard/api/inventory/keys/key-id/test"),
        Response(status_code=200),
    )


def test_inventory_reclassification_only_invalidates_when_applying_changes() -> None:
    preview = _request(
        "POST",
        "/dashboard/api/inventory/reclassify",
        "dry=true&scope=invalid",
    )
    default_preview = _request("POST", "/dashboard/api/inventory/reclassify")
    apply = _request(
        "POST",
        "/dashboard/api/inventory/reclassify",
        "dry=false&scope=invalid",
    )

    assert not _should_invalidate(preview, Response(status_code=200))
    assert not _should_invalidate(default_preview, Response(status_code=200))
    assert _should_invalidate(apply, Response(status_code=200))


def test_all_dashboard_api_mutations_use_invalidation_route() -> None:
    from janus.dashboard.api_v2 import router as api_v2_router
    from janus.dashboard.inventory_push_routes import router as inventory_push_router
    from janus.dashboard.inventory_routes import router as inventory_router
    from janus.dashboard.routes import router as dashboard_router

    mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}
    mutation_routes = [
        route
        for router in (dashboard_router, api_v2_router, inventory_router, inventory_push_router)
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods.intersection(mutation_methods)
    ]

    assert mutation_routes
    assert all(isinstance(route, DashboardMutationRoute) for route in mutation_routes)
