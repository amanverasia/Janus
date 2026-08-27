from __future__ import annotations

from importlib.resources import files
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
DASHBOARD = PROJECT_ROOT / "src" / "janus" / "dashboard"
STATIC = DASHBOARD / "static"
TEMPLATES = DASHBOARD / "templates"


def _cache_control_for(relative_path: str) -> str:
    from janus.app import _CachedDashboardStaticFiles

    mount = _CachedDashboardStaticFiles(directory=str(STATIC))
    full_path, stat_result = mount.lookup_path(relative_path.removeprefix("app/"))
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/dashboard/static/{relative_path}",
        "headers": [],
    }
    response = mount.file_response(full_path, stat_result, scope)
    return response.headers.get("cache-control", "")


def test_immutable_svelte_assets_are_cached_forever() -> None:
    immutable_dir = STATIC / "app" / "_app" / "immutable"
    assert immutable_dir.is_dir(), "Build dashboard-ui to emit _app/immutable assets"
    samples = [path for path in immutable_dir.rglob("*") if path.is_file()][:3]
    assert samples, "Expected at least one content-hashed immutable asset"
    for asset in samples:
        relative = asset.relative_to(STATIC).as_posix()
        cache_control = _cache_control_for(relative)
        assert "max-age=31536000" in cache_control, relative
        assert "immutable" in cache_control, relative


def test_index_html_and_version_json_stay_revalidatable() -> None:
    index_cache = _cache_control_for("app/index.html")
    version_cache = _cache_control_for("app/_app/version.json")
    assert index_cache == "no-cache"
    assert version_cache == "no-cache"


def test_login_is_the_only_server_rendered_dashboard_template() -> None:
    templates = sorted(path.name for path in TEMPLATES.glob("*.html"))
    assert templates == ["login.html"]

    source = (TEMPLATES / "login.html").read_text()
    assert "/dashboard/static/css/login.css" in source
    assert "<script" not in source
    assert "cdn." not in source


def test_login_stylesheet_is_a_package_resource() -> None:
    stylesheet = STATIC / "css" / "login.css"
    assert stylesheet.is_file()
    assert stylesheet.read_text().strip()
    package_root = files("janus.dashboard").joinpath("static")
    assert package_root.joinpath("css", "login.css").is_file()


def test_legacy_dashboard_assets_are_removed() -> None:
    for path in (
        STATIC / "css" / "dashboard.min.css",
        STATIC / "js" / "dashboard.js",
        STATIC / "vendor" / "htmx-2.0.4.min.js",
        STATIC / "vendor" / "chart.js-4.4.1.umd.min.js",
    ):
        assert not path.exists(), path
