from pathlib import Path

from janus.dashboard.live import get_bus, reset_bus

SVELTE_DIR = Path(__file__).parents[3] / "dashboard-ui" / "src"
MALICIOUS_PAYLOAD = '<img src=x onerror="window.__janusXss = true">'


def _svelte(relative_path: str) -> str:
    return (SVELTE_DIR / relative_path).read_text()


def test_untrusted_live_model_is_rendered_as_svelte_text() -> None:
    reset_bus()
    try:
        get_bus().record_completed(model=MALICIOUS_PAYLOAD, status=500)
        assert get_bus().snapshot()["recent"][0]["model"] == MALICIOUS_PAYLOAD

        source = _svelte("lib/pages/UsagePage.svelte")
        assert "{text(event.model ?? event.request_model)}" in source
        assert "{text(event.status ?? event.status_code, 'ok')}" in source
        assert "{@html" not in source
    finally:
        reset_bus()


def test_untrusted_toast_message_is_rendered_as_svelte_text() -> None:
    source = _svelte("lib/components/Toasts.svelte")

    assert "<span>{toast.message}</span>" in source
    assert "{@html" not in source


def test_untrusted_copilot_values_are_rendered_as_svelte_text() -> None:
    source = _svelte("lib/pages/ProvidersPage.svelte")

    assert "url.protocol === 'https:' && url.hostname === 'github.com'" in source
    assert "<code>{copilotUserCode}</code>" in source
    assert "href={copilotVerificationUri}" in source
    assert "{copilotStatus}" in source
    assert "{copilotError}" in source
    assert "{@html" not in source


def test_dashboard_svelte_sources_do_not_use_raw_html_directives() -> None:
    for path in SVELTE_DIR.rglob("*.svelte"):
        assert "{@html" not in path.read_text(), path
