from pathlib import Path

UI_SRC = Path(__file__).parents[3] / "dashboard-ui" / "src"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text()


def test_shell_renders_health_and_identity_from_state() -> None:
    source = _read("lib/components/Shell.svelte")
    # Hardcoded indicators must be gone; both widgets derive from props.
    assert "Administrator" not in source
    assert ">JA<" not in source
    assert "'JA'" not in source
    assert source.count("'System online'") == 1
    assert "{healthTitle}" in source
    assert "export let health" in source
    assert "export let identity" in source
    assert "system-dot {dotClass}" in source
    assert "{identityLabel}" in source


def test_shell_distinguishes_stale_health() -> None:
    source = _read("lib/components/Shell.svelte")
    assert "'stale'" in source
    assert "Stale: ${staleNotes.join" in source


def test_shell_dot_has_state_styles() -> None:
    source = _read("app.css")
    assert ".system-dot.unknown" in source
    assert ".system-dot.degraded" in source
    assert ".system-dot.offline" in source
    assert ".system-dot.stale" in source


def test_root_page_fetches_session_and_polls_health() -> None:
    source = _read("routes/+page.svelte")
    assert "/dashboard/api/session" in source
    assert "/dashboard/api/health" in source
    assert "HEALTH_POLL_MS" in source
    assert "{health}" in source
    assert "{identity}" in source


def test_overview_provider_health_badge_is_data_driven() -> None:
    source = _read("lib/pages/OverviewPage.svelte")
    assert "provider_health" in source
    assert "healthStatus === 'ok' ? 'Operational'" in source
    assert "healthStatus === 'degraded' ? 'Degraded'" in source
