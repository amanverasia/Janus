from __future__ import annotations

from importlib.resources import files
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
DASHBOARD = PROJECT_ROOT / "src" / "janus" / "dashboard"
STATIC = DASHBOARD / "static"
TEMPLATES = DASHBOARD / "templates"


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
