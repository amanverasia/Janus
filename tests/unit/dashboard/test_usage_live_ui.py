from pathlib import Path

SVELTE_DIR = Path(__file__).parents[3] / "dashboard-ui" / "src"
USAGE_PAGE = SVELTE_DIR / "lib" / "pages" / "UsagePage.svelte"


def _source() -> str:
    return USAGE_PAGE.read_text()


def test_live_view_initializes_from_sse_snapshot_only() -> None:
    source = _source()
    assert "usage/snapshot" not in source
    assert "api/usage/live" in source
    assert "Array.isArray(payload.recent)" in source


def test_stale_request_events_are_dropped_via_seq_watermark() -> None:
    source = _source()
    assert "let lastSeq = 0;" in source
    assert "if (seq <= lastSeq) return;" in source
    assert "if (payload.type === 'snapshot') lastSeq = number(payload.seq);" in source


def test_recent_activity_survives_reconnect_attempts() -> None:
    source = _source()
    # The reconnect path must not clear the rendered ring; only a fresh
    # snapshot from a re-established stream may replace it.
    assert "recent = []" not in source
    assert "connected = false" in source
