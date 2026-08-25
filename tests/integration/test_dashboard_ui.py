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
