from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings

AUTH_KEY = "dashboard-ui-auth-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}"}
APP_URL_PREFIX = "/dashboard/static/app/"
RESOURCE_REFERENCE_PATTERN = re.compile(r'(?:src|href)=["\']([^"\']+)["\']', re.IGNORECASE)


@pytest.fixture
def app(tmp_path):
    return create_app(
        config=JanusConfig(
            server=ServerSettings(port=0, data_dir=tmp_path),
            api_keys=[AUTH_KEY],
        )
    )


def _remote_transport(app):
    return ASGITransport(app=app, client=("203.0.113.10", 4321))


async def test_dashboard_ui_requires_the_same_non_loopback_authentication(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        legacy = await client.get("/dashboard/providers")
        shell = await client.get("/dashboard/ui")
        deep_link = await client.get("/dashboard/ui/inventory/keys")

    assert legacy.status_code == 303
    assert shell.status_code == legacy.status_code
    assert deep_link.status_code == legacy.status_code
    assert shell.headers["location"].startswith("/dashboard/login?next=/dashboard/ui")
    assert deep_link.headers["location"].startswith("/dashboard/login?next=/dashboard/ui/")


async def test_dashboard_ui_shell_and_deep_links_serve_the_current_app_shell(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        shell = await client.get("/dashboard/ui", headers=AUTH_HEADERS)
        deep_link = await client.get("/dashboard/ui/inventory/keys", headers=AUTH_HEADERS)

    assert shell.status_code == 200
    assert deep_link.status_code == 200
    assert shell.headers["content-type"].startswith("text/html")
    assert shell.headers["cache-control"] == "no-cache"
    assert deep_link.headers["cache-control"] == "no-cache"
    assert shell.content == deep_link.content
    assert b"<title>Janus" in shell.content


@pytest.mark.parametrize(
    ("legacy_path", "ui_path"),
    [
        ("/dashboard", "/dashboard/ui"),
        ("/dashboard/analytics", "/dashboard/ui/analytics"),
        ("/dashboard/budgets", "/dashboard/ui/budgets"),
        ("/dashboard/combos", "/dashboard/ui/combos"),
        ("/dashboard/inventory", "/dashboard/ui/inventory"),
        ("/dashboard/inventory/add", "/dashboard/ui/inventory/add"),
        ("/dashboard/inventory/import", "/dashboard/ui/inventory/import"),
        ("/dashboard/inventory/keys", "/dashboard/ui/inventory/keys"),
        ("/dashboard/keys", "/dashboard/ui/keys"),
        ("/dashboard/leaderboard", "/dashboard/ui/leaderboard"),
        ("/dashboard/pricing", "/dashboard/ui/pricing"),
        ("/dashboard/providers", "/dashboard/ui/providers"),
        ("/dashboard/request-logs", "/dashboard/ui/request-logs"),
        ("/dashboard/routing", "/dashboard/ui/routing"),
        ("/dashboard/savers", "/dashboard/ui/savers"),
        ("/dashboard/settings", "/dashboard/ui/settings"),
        ("/dashboard/tools", "/dashboard/ui/tools"),
        ("/dashboard/usage", "/dashboard/ui/usage"),
    ],
)
async def test_legacy_dashboard_pages_redirect_to_canonical_ui_route(
    app, legacy_path: str, ui_path: str
) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            f"{legacy_path}?sample=value",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )

    assert response.status_code == 308
    assert response.headers["location"] == f"{ui_path}?sample=value"


async def test_root_redirects_directly_to_canonical_dashboard_ui(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get("/", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/dashboard/ui"


async def test_legacy_dashboard_pages_are_not_advertised_in_openapi(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/dashboard" not in paths
    assert "/dashboard/providers" not in paths
    assert "/dashboard/settings" not in paths
    assert "/dashboard/api/v2/state/{section}" in paths


async def test_legacy_dashboard_trailing_slash_redirects_in_one_hop(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard/providers/?sample=value",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )

    assert response.status_code == 308
    assert response.headers["location"] == "/dashboard/ui/providers?sample=value"


async def test_svelte_assets_are_served_from_dashboard_static_app(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        index = await client.get(f"{APP_URL_PREFIX}index.html")

        assert index.status_code == 200
        assert index.headers["content-type"].startswith("text/html")
        assets = [
            reference
            for reference in RESOURCE_REFERENCE_PATTERN.findall(index.text)
            if reference.startswith(APP_URL_PREFIX)
        ]
        assert assets
        for asset in assets:
            response = await client.get(asset)
            assert response.status_code == 200, asset
            assert response.content, asset


async def test_dashboard_api_v2_is_not_captured_by_the_ui_fallback(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard/api/v2/state/overview",
            headers={**AUTH_HEADERS, "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["section"] == "overview"


async def test_inventory_api_is_not_captured_by_legacy_page_redirects(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard/api/inventory/providers",
            headers={**AUTH_HEADERS, "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json()["providers"], list)


async def test_svelte_immutable_assets_are_cached_forever(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        index = await client.get(f"{APP_URL_PREFIX}index.html")
        assets = [
            ref
            for ref in RESOURCE_REFERENCE_PATTERN.findall(index.text)
            if ref.startswith(APP_URL_PREFIX) and "_app/immutable/" in ref
        ]
        assert assets
        for asset in assets:
            response = await client.get(asset)
            assert response.status_code == 200, asset
            cache_control = response.headers["cache-control"]
            assert "max-age=31536000" in cache_control, asset
            assert "immutable" in cache_control, asset


async def test_svelte_html_and_version_json_stay_revalidatable(app) -> None:
    async with AsyncClient(transport=_remote_transport(app), base_url="http://test") as client:
        index = await client.get(f"{APP_URL_PREFIX}index.html")
        version = await client.get(f"{APP_URL_PREFIX}_app/version.json")

        assert index.status_code == 200
        assert version.status_code == 200
        assert index.headers["cache-control"] == "no-cache"
        assert version.headers["cache-control"] == "no-cache"
