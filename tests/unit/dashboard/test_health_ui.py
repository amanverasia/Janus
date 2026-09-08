from pathlib import Path

SVELTE_DIR = Path(__file__).parents[3] / "dashboard-ui" / "src"
SHELL = SVELTE_DIR / "lib" / "components" / "Shell.svelte"
ROOT_PAGE = SVELTE_DIR / "routes" / "+page.svelte"
OVERVIEW = SVELTE_DIR / "lib" / "pages" / "OverviewPage.svelte"
API = SVELTE_DIR / "lib" / "api.ts"


def _source(path: Path) -> str:
    return path.read_text()


def test_shell_renders_state_backed_health_instead_of_hardcoded_online():
    source = _source(SHELL)
    assert ">System online<" not in source
    assert ">Administrator<" not in source
    assert ">JA<" not in source
    for marker in (
        "Dashboard offline",
        "Connection stale",
        "System degraded",
        "System online",
    ):
        assert marker in source
    assert "system-dot {systemTone}" in source


def test_shell_identity_comes_from_health_label_only():
    source = _source(SHELL)
    assert "identityLabel = health.identity?.label ?? 'Dashboard session'" in source
    assert "{identityInitials}" in source
    assert "{identityLabel}" in source


def test_root_page_polls_health_and_passes_it_to_the_shell():
    source = _source(ROOT_PAGE)
    assert "getHealth" in source
    assert "HEALTH_POLL_MS = 30_000" in source
    assert "{health}" in source
    assert "{healthOffline}" in source
    api = _source(API)
    assert "'/dashboard/api/v2/health'" in api


def test_overview_provider_health_chip_is_derived_not_hardcoded():
    source = _source(OVERVIEW)
    assert 'class="status active">Operational' not in source
    assert "providerHealthTone" in source
    assert "providerHealthLabel" in source
    assert "'Degraded'" in source
