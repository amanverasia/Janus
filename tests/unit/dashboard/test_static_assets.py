from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings

PROJECT_ROOT = Path(__file__).parents[3]
DASHBOARD = PROJECT_ROOT / "src" / "janus" / "dashboard"
STATIC = DASHBOARD / "static"
TEMPLATES = DASHBOARD / "templates"
BLOCKED_ASSET_HOSTS = (
    "cdn.tailwindcss.com",
    "unpkg.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(TEMPLATES.glob("**/*.html")):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_dashboard_templates_use_only_local_runtime_assets() -> None:
    sources = {path.name: path.read_text() for path in TEMPLATES.glob("**/*.html")}
    combined = "\n".join(sources.values())

    for host in BLOCKED_ASSET_HOSTS:
        assert host not in combined

    assert "/dashboard/static/css/dashboard.min.css" in sources["base.html"]
    assert "/dashboard/static/vendor/htmx-2.0.4.min.js" in sources["base.html"]
    assert "/dashboard/static/vendor/chart.js-4.4.1.umd.min.js" in sources["base.html"]
    assert "/dashboard/static/css/dashboard.min.css" in sources["login.html"]
    assert "/dashboard/static/vendor/d3-7.9.0.min.js" in sources["analytics.html"]
    assert "/dashboard/static/vendor/d3-sankey-0.12.3.min.js" in sources["analytics.html"]

    references = set(re.findall(r"/dashboard/static/([^\"']+)", combined))
    assert references
    for reference in references:
        assert (STATIC / reference).is_file(), reference


def test_vendored_assets_match_manifest_and_are_package_resources() -> None:
    manifest = json.loads((STATIC / "vendor" / "manifest.json").read_text())
    package_root = files("janus.dashboard").joinpath("static")

    for relative_path, expected_sha256 in manifest["files"].items():
        path = STATIC / relative_path
        assert path.is_file(), relative_path
        assert _sha256(path) == expected_sha256, relative_path
        assert package_root.joinpath(*Path(relative_path).parts).is_file(), relative_path


def test_tailwind_bundle_matches_current_inputs() -> None:
    manifest = json.loads((STATIC / "vendor" / "manifest.json").read_text())
    tailwind = manifest["tailwind"]

    assert tailwind["templates_sha256"] == _template_digest()
    assert tailwind["input_sha256"] == _sha256(STATIC / "css" / "tailwind.input.css")
    assert tailwind["config_sha256"] == _sha256(
        PROJECT_ROOT / "scripts" / "tailwind.dashboard.config.js"
    )

    css = (STATIC / "css" / "dashboard.min.css").read_text()
    assert ".bg-green-900" in css
    assert ".z-\\[100\\]" in css
    assert ".md\\:hidden" in css


async def test_vendored_assets_are_served_by_the_dashboard_static_mount(tmp_path: Path) -> None:
    app = create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    paths = (
        "/dashboard/static/css/dashboard.min.css",
        "/dashboard/static/vendor/htmx-2.0.4.min.js",
        "/dashboard/static/vendor/chart.js-4.4.1.umd.min.js",
        "/dashboard/static/vendor/d3-7.9.0.min.js",
        "/dashboard/static/vendor/d3-sankey-0.12.3.min.js",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.content, path
