import re
from pathlib import Path

UI_SRC = Path(__file__).parents[3] / "dashboard-ui" / "src"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text()


def _all_style_sources() -> list[tuple[str, str]]:
    sources = [("app.css", _read("app.css"))]
    for folder in ("pages", "components"):
        sources += [
            (path.name, path.read_text())
            for path in (UI_SRC / "lib" / folder).glob("*.svelte")
        ]
    return sources


def test_no_sub_11px_font_sizes_remain() -> None:
    # The previous pattern matched only 9px and 10px, so six 8px declarations --
    # smaller than what it banned -- passed this guard.
    offenders = [
        f"{name}: {match.group(0)}"
        for name, source in _all_style_sources()
        for match in re.finditer(r"font-size:\s*(?:[0-9]|10)px\b", source)
    ]
    assert not offenders, offenders


def test_datatable_omits_empty_actions_column() -> None:
    source = _read("lib/components/DataTable.svelte")
    assert "$$slots.actions" in source
    assert "{#if hasActions}" in source
    assert source.count("{#if hasActions}") == 2


def test_command_palette_supports_arrow_key_selection() -> None:
    source = _read("lib/components/CommandPalette.svelte")
    assert "case 'ArrowDown':" in source
    assert "case 'ArrowUp':" in source
    assert "case 'Home':" in source
    assert "case 'End':" in source
    assert "results[activeIndex]" in source
    assert "aria-activedescendant" in source
    assert 'role="option"' in source
    assert "aria-selected={index === activeIndex}" in source


def test_response_error_parses_structured_json() -> None:
    source = _read("lib/api.ts")
    assert "JSON.parse" in source
    for key in ("detail", "error", "message"):
        assert f"'{key}'" in source


def test_leaderboard_has_visible_time_range_control() -> None:
    source = _read("lib/pages/LeaderboardPage.svelte")
    assert 'aria-label="Time range"' in source
    assert "navigateQuery({ days:" in source


def test_tools_examples_are_authentication_aware() -> None:
    source = _read("lib/pages/ToolsPage.svelte")
    assert "require_api_key" in source
    assert "API key required" in source
    assert "No API key required" in source
    assert "$JANUS_API_KEY" in source
