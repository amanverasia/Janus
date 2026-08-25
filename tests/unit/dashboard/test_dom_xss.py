from pathlib import Path

from janus.dashboard.live import get_bus, reset_bus

TEMPLATE_DIR = Path(__file__).parents[3] / "src" / "janus" / "dashboard" / "templates"
MALICIOUS_PAYLOAD = '<img src=x onerror="window.__janusXss = true">'


def _template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text()


def test_untrusted_live_model_is_rendered_as_text() -> None:
    reset_bus()
    try:
        get_bus().record_completed(model=MALICIOUS_PAYLOAD, status=500)
        assert get_bus().snapshot()["recent"][0]["model"] == MALICIOUS_PAYLOAD

        source = _template("overview.html")
        assert "model.textContent = ev.model || '—';" in source
        assert "status.textContent = ev.status || '';" in source
        assert "feed.replaceChildren(fragment);" in source
        assert "feed.innerHTML" not in source
    finally:
        reset_bus()


def test_untrusted_toast_message_is_rendered_as_text() -> None:
    source = _template("base.html")

    assert "messageEl.textContent = message;" in source
    assert "el.append(messageEl, closeButton);" in source
    assert "el.innerHTML" not in source


def test_untrusted_copilot_values_are_rendered_as_text() -> None:
    source = _template("providers.html")

    assert "destination.textContent = verificationUri" in source
    assert "code.textContent = userCode" in source
    assert "result.textContent = message;" in source
    assert "url.protocol === 'https:' || url.protocol === 'http:'" in source
    assert "statusEl.innerHTML" not in source
