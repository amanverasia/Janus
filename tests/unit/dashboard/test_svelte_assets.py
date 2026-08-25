from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[3]
DASHBOARD_UI_ROOT = PROJECT_ROOT / "dashboard-ui"
APP_ROOT = PROJECT_ROOT / "src" / "janus" / "dashboard" / "static" / "app"
APP_URL_PREFIX = "/dashboard/static/app/"
CDN_HOST_PATTERN = re.compile(
    r"https?://(?:cdn\\.tailwindcss\\.com|unpkg\\.com|cdn\\.jsdelivr\\.net|"
    r"cdnjs\\.cloudflare\\.com|esm\\.sh|cdn\\.skypack\\.dev|fonts\\.googleapis\\.com|"
    r"fonts\\.gstatic\\.com)(?:[/:?]|$)",
    re.IGNORECASE,
)
RESOURCE_REFERENCE_PATTERN = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _emitted_files() -> list[Path]:
    return sorted(path for path in APP_ROOT.rglob("*") if path.is_file())


def _index_contents() -> str:
    index = APP_ROOT / "index.html"
    assert index.is_file(), (
        "Build dashboard-ui and commit src/janus/dashboard/static/app/index.html"
    )
    return index.read_text(encoding="utf-8")


def test_committed_svelte_bundle_is_packaged_with_its_emitted_assets() -> None:
    emitted_files = _emitted_files()

    _index_contents()
    assert emitted_files
    assert any(path.suffix == ".js" for path in emitted_files)

    package_app = files("janus.dashboard").joinpath("static", "app")
    for path in emitted_files:
        relative_path = path.relative_to(APP_ROOT)
        assert package_app.joinpath(*relative_path.parts).is_file(), relative_path.as_posix()


def test_svelte_index_uses_local_assets_and_has_mobile_document_metadata() -> None:
    index = _index_contents()
    references = RESOURCE_REFERENCE_PATTERN.findall(index)
    local_assets = [reference for reference in references if reference.startswith(APP_URL_PREFIX)]

    assert re.search(
        r'<meta[^>]+name=["\']viewport["\'][^>]+content=["\'][^"\']*initial-scale=1',
        index,
        re.IGNORECASE,
    )
    assert re.search(r'<meta[^>]+name=["\']theme-color["\']', index, re.IGNORECASE)
    assert re.search(r'<meta[^>]+name=["\']description["\']', index, re.IGNORECASE)
    assert re.search(r"<title>Janus(?: Dashboard)?</title>", index, re.IGNORECASE)
    assert local_assets

    for reference in local_assets:
        relative_path = reference.removeprefix(APP_URL_PREFIX).split("?", maxsplit=1)[0]
        assert (APP_ROOT / relative_path).is_file(), reference


def test_svelte_bundle_has_no_external_runtime_cdn_dependencies() -> None:
    emitted_files = _emitted_files()
    assert emitted_files

    for path in emitted_files:
        contents = path.read_text(encoding="utf-8", errors="ignore")
        assert CDN_HOST_PATTERN.search(contents) is None, path.relative_to(APP_ROOT).as_posix()


def test_svelte_api_key_copy_uses_clipboard_fallback_and_raw_prefix() -> None:
    helper = (DASHBOARD_UI_ROOT / "src" / "lib" / "clipboard.ts").read_text(encoding="utf-8")
    keys_page = (DASHBOARD_UI_ROOT / "src" / "lib" / "pages" / "KeysPage.svelte").read_text(
        encoding="utf-8"
    )

    assert "navigator.clipboard?.writeText" in helper
    assert "document.execCommand('copy')" in helper
    assert "row.prefix ?? row.key_prefix" in keys_page
    assert "Copy key prefix" in keys_page
