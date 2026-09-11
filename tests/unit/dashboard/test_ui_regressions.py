"""Regressions for dashboard UI defects found in the 2026-09-11 frontend audit.

Each test names the defect it guards. The dashboard UI has no JS test runner, so
these follow the established pattern in ``test_a11y_qol_ui.py`` and assert against
the Svelte/CSS sources that ship in the committed bundle.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_SRC = Path(__file__).parents[3] / "dashboard-ui" / "src"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text()


def _app_css() -> str:
    return _read("app.css")


def _index_of(source: str, needle: str) -> int:
    index = source.find(needle)
    assert index != -1, f"expected to find {needle!r}"
    return index


# --- CSS correctness -------------------------------------------------------


def test_mobile_close_button_stays_hidden_on_desktop() -> None:
    """`.icon-button{display:grid}` outranked `.mobile-close{display:none}`.

    Both were single-class selectors, so the later rule won and a stray close
    button rendered inside the sidebar at every viewport width.
    """
    css = _app_css()
    assert ".sidebar .mobile-close" in css, (
        "mobile-close needs a selector that outranks .icon-button"
    )


def test_nav_scrim_has_base_styles_outside_the_mobile_media_query() -> None:
    """Opening the mobile nav then widening the viewport left an unstyled button.

    `.nav-scrim` was only ever styled inside `@media (max-width: 820px)`.
    """
    css = _app_css()
    scrim = _index_of(css, ".nav-scrim")
    mobile_query = _index_of(css, "@media (max-width: 820px)")
    assert scrim < mobile_query, ".nav-scrim needs base styles before the mobile query"


def test_checkbox_fields_can_span_the_full_form_grid() -> None:
    """`.check-field full` never matched, because only `.field.full` was defined."""
    assert ".check-field.full" in _app_css()


def test_full_height_shells_use_dynamic_viewport_units() -> None:
    """`100vh` overflows on mobile browsers with a collapsing address bar."""
    css = _app_css()
    assert "100dvh" in css
    assert "min-height: 100vh" not in css


def test_page_header_controls_are_styled() -> None:
    """Selects in `.page-actions` fell back to raw browser chrome."""
    assert ".page-actions select" in _app_css()


def test_modal_body_height_accounts_for_its_header() -> None:
    """`max-height: 72vh` on the body alone clipped modals on landscape phones."""
    css = _app_css()
    assert "max-height: 72vh" not in css, "modal height must account for the header"


def test_tab_strips_and_filter_bars_survive_narrow_viewports() -> None:
    """`width: max-content` with no wrap or scroll overflowed the page."""
    css = _app_css()
    tabs = css[_index_of(css, ".tabs {") : _index_of(css, ".tabs {") + 260]
    assert "max-width: 100%" in tabs and "overflow-x: auto" in tabs


# --- Legibility and hierarchy ---------------------------------------------


def test_settings_labels_do_not_outrank_their_section_headings() -> None:
    """`.setting-toggle strong` and `.reset-copy` set no size and fell back to 16px,

    rendering larger than the 14px panel headings above them.
    """
    source = _read("lib/pages/SettingsPage.svelte")
    block = source[_index_of(source, "<style>") :]
    for selector in (".setting-toggle strong", ".reset-copy"):
        rule_start = _index_of(block, selector)
        rule = block[rule_start : block.index("}", rule_start)]
        assert "font-size" in rule, f"{selector} must set an explicit font-size"


# --- Formatting ------------------------------------------------------------


def test_currency_renders_without_a_locale_dependent_country_prefix() -> None:
    """`Intl.NumberFormat(undefined, ...)` rendered `US$0.00` outside en-US."""
    source = _read("lib/data.ts")
    assert "narrowSymbol" in source, "money() must pin currencyDisplay to narrowSymbol"


def test_percentages_reject_non_numeric_input() -> None:
    """Leaderboard used bare `Number(...).toFixed(1)` and rendered `NaN%`."""
    source = _read("lib/pages/LeaderboardPage.svelte")
    assert "Number(row.success_pct" not in source


# --- Shared components -----------------------------------------------------


def test_mini_chart_gradient_ids_are_unique_per_instance() -> None:
    """A hardcoded gradient id breaks the moment two charts share a page."""
    source = _read("lib/components/MiniChart.svelte")
    assert 'id="cloudline-chart"' not in source


def test_mini_chart_renders_an_empty_state_instead_of_a_fake_line() -> None:
    """With no data it drew a flat line pinned to the baseline, reading as real."""
    source = _read("lib/components/MiniChart.svelte")
    assert "hasData" in source


def test_modal_label_ids_are_unique_per_instance() -> None:
    """A hardcoded `modal-title` id duplicates as soon as a page has two modals."""
    source = _read("lib/components/Modal.svelte")
    assert 'id="modal-title"' not in source


def test_modal_does_not_close_when_a_drag_ends_on_the_backdrop() -> None:
    """Selecting text inside the modal and releasing outside dismissed it."""
    source = _read("lib/components/Modal.svelte")
    assert "mousedown" in source


def test_toasts_are_dismissible_and_do_not_reannounce_the_stack() -> None:
    """`aria-atomic="true"` re-read every toast on each change, and none could be closed."""
    source = _read("lib/components/Toasts.svelte")
    assert 'aria-atomic="false"' in source
    assert "dismiss" in source.lower()


def test_data_table_rows_are_keyed() -> None:
    """Unkeyed rows let Svelte reuse DOM across sorts, leaking action-slot state."""
    source = _read("lib/components/DataTable.svelte")
    assert re.search(r"\{#each rows as row[^}]*\(", source)


def test_alert_links_navigate_without_a_full_page_reload() -> None:
    """`<a href>` bypassed the SPA router and reloaded the whole bundle."""
    source = _read("lib/components/AlertStrip.svelte")
    assert "preventDefault" in source


def test_command_palette_keeps_keyboard_control_when_a_result_has_focus() -> None:
    """`.palette` stopped keydown propagation, so arrows and Escape went dead."""
    source = _read("lib/components/CommandPalette.svelte")
    assert "on:keydown|stopPropagation" not in source


def test_command_palette_traps_and_restores_focus() -> None:
    """Tab escaped to the page behind, and focus never returned to the trigger."""
    source = _read("lib/components/CommandPalette.svelte")
    assert "Tab" in source
    assert "restoreFocus" in source or "previouslyFocused" in source


# --- Page logic ------------------------------------------------------------


def test_response_health_bars_use_their_severity_tone() -> None:
    """Each row carried a tone at index 2 that was computed and never rendered."""
    source = _read("lib/pages/AnalyticsPage.svelte")
    assert "row[2]" in source


def test_creating_a_combo_from_the_empty_state_clears_any_prior_edit() -> None:
    """`editing` survived, so the create form could PUT to a deleted combo id."""
    source = _read("lib/pages/CombosPage.svelte")
    empty_state = source[_index_of(source, "<EmptyState") : _index_of(source, "</EmptyState>")]
    assert "editing = undefined" in empty_state


def test_the_selected_model_group_can_be_collapsed() -> None:
    """`isCollapsed` short-circuited on the selected key, so it never collapsed."""
    source = _read("lib/pages/ModelsPage.svelte")
    assert "if (group.key === selectedGroupKey) return false;" not in source


def test_editing_an_api_key_prefills_its_daily_budget() -> None:
    """Name and models prefilled; daily budget silently rendered empty."""
    source = _read("lib/pages/KeysPage.svelte")
    start = _index_of(source, 'name="daily_budget"')
    element = source[source.rindex("<input", 0, start) : source.index("/>", start)]
    assert "value=" in element, f"daily_budget input has no value binding: {element}"


def test_modal_submit_handlers_surface_failures() -> None:
    """Budgets and Combos awaited `action()` with no catch, so failures were unhandled."""
    for page in ("BudgetsPage.svelte", "CombosPage.svelte"):
        source = _read(f"lib/pages/{page}")
        assert "catch" in source, page


def test_request_log_detail_errors_are_styled() -> None:
    """`.file-error` is defined only in another component's scoped block."""
    source = _read("lib/pages/RequestLogsPage.svelte")
    assert "file-error" not in source, "use a class this component actually styles"


def test_request_log_inspection_cannot_race() -> None:
    """Rapid clicks resolved out of order and showed the wrong row's detail."""
    source = _read("lib/pages/RequestLogsPage.svelte")
    assert "AbortController" in source or "requestId" in source


def test_settings_reject_out_of_range_values_before_saving() -> None:
    """Number inputs saved on change with no validity check, persisting bad values."""
    source = _read("lib/pages/SettingsPage.svelte")
    assert "checkValidity" in source


# --- Shell and navigation --------------------------------------------------


def test_sidebar_links_support_modified_clicks() -> None:
    """`on:click|preventDefault` swallowed Ctrl/Cmd-click, blocking new tabs."""
    source = _read("lib/components/Shell.svelte")
    nav = source[_index_of(source, "<nav ") : _index_of(source, "</nav>")]
    assert "on:click|preventDefault" not in nav, (
        "sidebar links must let modified clicks reach the browser"
    )


def test_sidebar_closes_on_escape() -> None:
    source = _read("lib/components/Shell.svelte")
    assert "Escape" in source


def test_logging_out_is_not_the_primary_profile_action() -> None:
    """The whole identity chip was a logout button; a stray click ended the session."""
    source = _read("lib/components/Shell.svelte")
    assert "class=\"profile\" on:click={() => dispatch('logout')}" not in source


def test_aggregate_spend_is_not_formatted_as_a_per_token_rate() -> None:
    """Live dashboard showed "US$26.4929" for Spend today.

    money() used maximumFractionDigits: 4 for everything, so aggregate currency
    inherited the precision that only per-MTok rates need.
    """
    source = _read("lib/data.ts")
    money_block = source[_index_of(source, "export const money") :][:400]
    assert "maximumFractionDigits: 4" not in money_block, "aggregates are money, not rates"
    assert "export const rate" in source, "per-MTok pricing needs its own 4dp formatter"


def test_pricing_columns_use_the_rate_formatter() -> None:
    """Per-MTok values genuinely need 4dp and must not be rounded to cents."""
    source = _read("lib/pages/PricingPage.svelte")
    assert "format: rate" in source


def test_setup_checklist_hides_once_the_gateway_is_configured() -> None:
    """It rendered whenever the server sent the object, so a fully configured

    gateway kept a permanent "Gateway readiness" panel of all-green items.
    """
    source = _read("lib/pages/OverviewPage.svelte")
    assert "checklistComplete" in source
