#!/usr/bin/env python3
"""Browser regression suite for the dashboard SPA (#110).

Extends the route smoke with interaction coverage: hub section tabs, legacy
redirects, back/forward navigation, filters, server-side pagination, a
reversible CRUD mutation, empty-state rendering, and a Connect preview that
must not import or echo the pasted credential, plus a no-secret DOM scan
across every route. Drives a real
Playwright browser against a running Janus server.

Environment:
    JANUS_SMOKE_BASE_URL  base URL of the running server
                          (default http://127.0.0.1:20131)
    JANUS_SMOKE_API_KEY   Janus API key with dashboard access (required)

Exit code 0 means every scenario passed.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from collections.abc import Callable

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

BASE_URL = os.environ.get("JANUS_SMOKE_BASE_URL", "http://127.0.0.1:20131")
API_KEY = os.environ.get("JANUS_SMOKE_API_KEY", "")

ROUTES: list[tuple[str, str]] = [
    ("/dashboard/ui", "Overview"),
    ("/dashboard/ui/usage", "Usage"),
    ("/dashboard/ui/analytics", "Analytics"),
    ("/dashboard/ui/leaderboard", "Leaderboard"),
    ("/dashboard/ui/request-logs", "Request logs"),
    ("/dashboard/ui/inventory", "Inventory"),
    ("/dashboard/ui/inventory/keys", "Inventory keys"),
    ("/dashboard/ui/connect", "Connect"),
    ("/dashboard/ui/connect/restore", "Restore backup"),
    ("/dashboard/ui/providers", "Providers"),
    ("/dashboard/ui/models", "Models"),
    ("/dashboard/ui/models/unreachable", "Models you can't reach"),
    ("/dashboard/ui/combos", "Combos"),
    ("/dashboard/ui/routing", "Routing"),
    ("/dashboard/ui/savers", "Token savers"),
    ("/dashboard/ui/budgets", "Budgets"),
    ("/dashboard/ui/keys", "API keys"),
    ("/dashboard/ui/tools", "Tools"),
    ("/dashboard/ui/pricing", "Pricing"),
    ("/dashboard/ui/settings", "Settings"),
]

HUB_TABS: list[tuple[str, str, list[str]]] = [
    ("/dashboard/ui/connect", "Connect", ["Keys and logins", "Restore backup"]),
    ("/dashboard/ui/inventory", "Inventory", ["Overview", "Keys"]),
    (
        "/dashboard/ui/providers",
        "Routing",
        ["Providers", "Models", "Can't reach", "Combos", "Health", "Token savers"],
    ),
    ("/dashboard/ui/usage", "Usage", ["Live", "Analytics", "Leaderboard", "Request logs"]),
    ("/dashboard/ui/settings", "Settings", ["General", "API keys", "Budgets", "Pricing", "Tools"]),
]

LEGACY_REDIRECTS: list[tuple[str, str]] = [
    ("/dashboard/inventory/add", "/dashboard/ui/connect"),
    ("/dashboard/inventory/import", "/dashboard/ui/connect/restore"),
    ("/dashboard/ui/inventory/add", "/dashboard/ui/connect"),
    ("/dashboard/ui/inventory/import", "/dashboard/ui/connect/restore"),
]

LOGIN_WAIT_MS = 15_000
ROUTE_WAIT_MS = 20_000

# A full (unmasked) Janus key or an AWS-style access key in the rendered DOM
# would be a credential leak. Masked displays like ``sk-s****tatic`` do not
# match these patterns.
SECRET_PATTERNS = [
    re.compile(r"sk-janus-[0-9a-f]{32}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def _login(page: Page, failures: list[str]) -> bool:
    response = page.goto(f"{BASE_URL}/dashboard/login", wait_until="domcontentloaded")
    if response is None or not response.ok:
        failures.append(f"login page returned {response.status if response else 'no response'}")
        return False
    page.fill('input[name="api_key"]', API_KEY)
    page.click('button[type="submit"]')
    try:
        page.wait_for_url("**/dashboard/ui**", timeout=LOGIN_WAIT_MS)
    except PlaywrightTimeoutError:
        failures.append("login did not reach /dashboard/ui")
        return False
    return True


def _visit(page: Page, path: str, *, wait_ms: int = ROUTE_WAIT_MS) -> bool:
    response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("main", state="visible", timeout=wait_ms)
    except PlaywrightTimeoutError:
        return False
    return response is not None and response.ok


def _secrets_in_dom(page: Page) -> list[str]:
    """Return any full secret patterns found in the rendered DOM text."""
    body = page.inner_text("body")
    found: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.findall(body):
            found.append(match)
    if API_KEY and API_KEY in body:
        found.append(API_KEY)
    return found


def scenario_render_and_no_leak(page: Page) -> list[str]:
    failures: list[str] = []
    for path, label in ROUTES:
        started = time.monotonic()
        try:
            ok = _visit(page, path)
            title = page.title() or ""
            if not ok:
                failures.append(f"{path}: HTTP/render failed")
            elif "Sign in" in title:
                failures.append(f"{path}: bounced back to sign-in")
            else:
                leaked = _secrets_in_dom(page)
                if leaked:
                    failures.append(f"{path}: secret leaked into DOM: {leaked}")
                else:
                    elapsed = int((time.monotonic() - started) * 1000)
                    print(f"  render {path} ({label}) in {elapsed}ms")
        except Exception as exc:  # noqa: BLE001 - report every route failure
            failures.append(f"{path}: {type(exc).__name__}: {exc}")
    return failures


def scenario_hub_tabs(page: Page) -> list[str]:
    failures: list[str] = []
    for path, hub, expected in HUB_TABS:
        try:
            _visit(page, path)
            tabs = page.locator(f"nav.section-tabs[aria-label='{hub} sections'] a")
            tabs.first.wait_for(state="visible", timeout=ROUTE_WAIT_MS)
            labels = [label.strip() for label in tabs.all_inner_texts()]
            if labels != expected:
                failures.append(f"{hub} tabs: expected {expected}, got {labels}")
                continue
            target = tabs.nth(len(expected) - 1)
            href = target.get_attribute("href") or ""
            target.click()
            page.wait_for_url(f"**{href}", timeout=ROUTE_WAIT_MS)
            if target.get_attribute("aria-current") != "page":
                failures.append(f"{hub} tabs: {expected[-1]} not marked current after click")
            else:
                print(f"  hub {hub}: {len(expected)} tabs OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{hub} tabs: {type(exc).__name__}: {exc}")
    return failures


def scenario_legacy_redirects(page: Page) -> list[str]:
    failures: list[str] = []
    for legacy, target in LEGACY_REDIRECTS:
        try:
            _visit(page, legacy)
            page.wait_for_url(f"**{target}", timeout=ROUTE_WAIT_MS)
            print(f"  redirect {legacy} -> {target} OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"redirect {legacy}: landed on {page.url} ({type(exc).__name__})")
    return failures


def _inventory_total(page: Page) -> int:
    response = page.request.get(f"{BASE_URL}/dashboard/api/inventory/keys?limit=1")
    if not response.ok:
        raise RuntimeError(f"inventory keys API returned {response.status}")
    return int(response.json()["total"])


def scenario_connect_preview_without_import(page: Page) -> list[str]:
    """Pasting a key previews it masked, never imports it, never echoes it."""
    failures: list[str] = []
    raw_key = f"gsk_regression{secrets.token_hex(20)}"
    try:
        before = _inventory_total(page)
        _visit(page, "/dashboard/ui/connect")
        page.fill("#connect-keys", raw_key)
        chips = page.locator("ul.summary-chips[aria-label='What Janus found']")
        chips.wait_for(state="visible", timeout=ROUTE_WAIT_MS)
        if "Groq" not in chips.inner_text():
            failures.append(f"connect preview chips missing Groq: {chips.inner_text()!r}")
        if raw_key in page.content() or raw_key in page.locator(".preview").inner_text():
            failures.append("connect preview echoed the raw key into the DOM")
        import_button = page.locator("button.primary:has-text('Import')")
        if import_button.count() == 0 or not import_button.first.is_enabled():
            failures.append("connect preview did not enable Import for a new key")
        page.fill("#connect-keys", "")
        page.wait_for_timeout(500)
        after = _inventory_total(page)
        if after != before:
            failures.append(f"connect preview wrote to inventory ({before} -> {after} keys)")
        if not failures:
            print("  connect preview (no import, no echo) OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"connect preview: {type(exc).__name__}: {exc}")
    return failures


def scenario_restore_export_download(page: Page) -> list[str]:
    failures: list[str] = []
    try:
        _visit(page, "/dashboard/ui/connect/restore")
        with page.expect_download(timeout=ROUTE_WAIT_MS) as download_info:
            page.get_by_role("button", name="Export current inventory").click()
        download = download_info.value
        if not download.suggested_filename.endswith(".json"):
            failures.append(f"restore export filename: {download.suggested_filename}")
        else:
            print(f"  restore export downloaded {download.suggested_filename}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"restore export: {type(exc).__name__}: {exc}")
    return failures


def scenario_unreachable_connect_link(page: Page) -> list[str]:
    """A "Connect <provider>" card opens Connect with that provider preselected."""
    failures: list[str] = []
    try:
        _visit(page, "/dashboard/ui/models/unreachable")
        buttons = page.locator("button:has-text('Connect ')")
        if buttons.count() == 0:
            print("  unreachable connect link: no connect-kind group (skipped)")
            return failures
        buttons.first.click()
        page.wait_for_url("**/dashboard/ui/connect?provider=*", timeout=ROUTE_WAIT_MS)
        provider = page.url.split("provider=")[-1].split("&")[0]
        select = page.locator("select")
        select.first.wait_for(state="visible", timeout=ROUTE_WAIT_MS)
        page.wait_for_function(
            "expected => document.querySelector('select')?.value === expected", arg=provider
        )
        if page.locator("select").first.input_value() != provider:
            failures.append(f"connect preselect: select value != {provider!r}")
        else:
            print(f"  unreachable connect link -> connect?provider={provider} preselected OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"unreachable connect link: {type(exc).__name__}: {exc}")
    return failures


def scenario_back_forward_navigation(page: Page) -> list[str]:
    failures: list[str] = []
    try:
        _visit(page, "/dashboard/ui/pricing")
        _visit(page, "/dashboard/ui/models")
        page.go_back()
        page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
        if not page.url.rstrip("/").endswith("/dashboard/ui/pricing"):
            failures.append(f"back navigation landed on {page.url}, expected /pricing")
        page.go_forward()
        page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
        if not page.url.rstrip("/").endswith("/dashboard/ui/models"):
            failures.append(f"forward navigation landed on {page.url}, expected /models")
        else:
            print("  nav back/forward pricing <-> models OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"back/forward: {type(exc).__name__}: {exc}")
    return failures


def _wait_for_text(page: Page, needle: str, *, timeout_ms: int = ROUTE_WAIT_MS) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if needle in page.inner_text("body"):
            return True
        page.wait_for_timeout(200)
    return False


def scenario_filter_and_empty_state(page: Page) -> list[str]:
    """A server-side filter narrows results; a no-match query renders cleanly.

    Drives the filter through the URL (the same path the search box navigates
    to via ``navigateQuery``) so the assertion is deterministic rather than
    racing Svelte's ``searchDirty`` input sync under synthetic key events.
    """
    failures: list[str] = []
    try:
        _visit(page, "/dashboard/ui/models?search=zzzz-no-such-model")
        if "search=zzzz-no-such-model" not in page.url:
            failures.append("models no-match search did not preserve the URL query")
        if "Couldn’t load" in page.inner_text("body"):
            failures.append("models no-match query rendered an error state")
        token = "openai-model-000"
        _visit(page, f"/dashboard/ui/models?search={token}")
        page.wait_for_function(
            "document.querySelector('main .model-row .model-name code') "
            "|| document.body.innerText.includes('No models match')",
            timeout=ROUTE_WAIT_MS,
        )
        matching_results = page.locator("main .model-row .model-name code").filter(has_text=token)
        if matching_results.count() == 0:
            failures.append("models matching query did not surface the model")
        _visit(page, "/dashboard/ui/models?offset=150&limit=25")
        page.wait_for_selector("main .model-provider-rail", timeout=ROUTE_WAIT_MS)
        page.wait_for_timeout(400)
        if page.get_by_role("button", name="Next").count():
            failures.append("models overview rendered a pager (#245)")
        if not failures:
            print("  filter + empty state OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"filter: {type(exc).__name__}: {exc}")
    return failures


def scenario_pagination(page: Page) -> list[str]:
    """Server-side pagination advances the page and the URL offset."""
    failures: list[str] = []
    for path, name in [("/dashboard/ui/pricing", "pricing")]:
        try:
            _visit(page, path)
            page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
            # Wait for SPA hydration so Next is present on production-size catalogs.
            page.wait_for_timeout(800)
            next_btn = page.get_by_role("button", name="Next")
            if next_btn.count() == 0 or not next_btn.first.is_enabled():
                print(f"  pagination {name}: skipped (fewer rows than a page)")
                continue
            before_url = page.url
            next_btn.first.click()
            page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
            page.wait_for_timeout(400)
            if "offset=" not in page.url:
                failures.append(f"{name}: Next did not add an offset query param")
                continue
            if page.url == before_url:
                failures.append(f"{name}: Next did not advance the URL")
            prev_btn = page.get_by_role("button", name="Previous")
            if prev_btn.count() and prev_btn.first.is_enabled():
                prev_btn.first.click()
                page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
                page.wait_for_timeout(400)
            print(f"  pagination {name} Next/Previous OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"pagination {name}: {type(exc).__name__}: {exc}")
    return failures


def scenario_pricing_override_crud(page: Page) -> list[str]:
    """A reversible create-then-delete mutation exercises the action path."""
    failures: list[str] = []
    model_id = "regression-override-delete-me"
    try:
        _visit(page, "/dashboard/ui/pricing")
        page.locator("button:has-text('Add override')").first.click()
        page.wait_for_selector("input[name='model']", timeout=ROUTE_WAIT_MS)
        page.fill("input[name='model']", model_id)
        page.fill("input[name='input_per_mtok']", "1")
        page.fill("input[name='output_per_mtok']", "2")
        page.locator("button:has-text('Save override')").first.click()
        appeared = _wait_for_text(page, model_id, timeout_ms=ROUTE_WAIT_MS)
        if not appeared:
            failures.append("pricing override did not appear after save")
        else:
            row = page.locator(f"tr:has-text('{model_id}')")
            row.locator("button[title='Delete override']").click()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and model_id in page.inner_text("body"):
                page.wait_for_timeout(200)
            if model_id in page.inner_text("body"):
                failures.append("pricing override was not deleted")
            else:
                print("  pricing override create + delete OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"pricing override CRUD: {type(exc).__name__}: {exc}")
    finally:
        # Never leave the regression override behind on a production-size DB.
        try:
            if model_id in page.inner_text("body"):
                row = page.locator(f"tr:has-text('{model_id}')")
                if row.count():
                    row.first.locator("button[title='Delete override']").click()
                    page.wait_for_timeout(800)
        except Exception:  # noqa: BLE001
            pass
    return failures


SCENARIOS: list[tuple[str, Callable[[Page], list[str]]]] = [
    ("render + no-secret DOM", scenario_render_and_no_leak),
    ("hub section tabs", scenario_hub_tabs),
    ("legacy redirects", scenario_legacy_redirects),
    ("connect preview without import", scenario_connect_preview_without_import),
    ("restore export download", scenario_restore_export_download),
    ("unreachable connect link", scenario_unreachable_connect_link),
    ("back/forward navigation", scenario_back_forward_navigation),
    ("filter + empty state", scenario_filter_and_empty_state),
    ("pagination", scenario_pagination),
    ("mutation (pricing override CRUD)", scenario_pricing_override_crud),
]


def run() -> int:
    if not API_KEY:
        print("JANUS_SMOKE_API_KEY is required", file=sys.stderr)
        return 2

    all_failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        # Auto-accept window.confirm so the delete step in the CRUD scenario
        # does not block on an unhandled dialog.
        page.on("dialog", lambda dialog: dialog.accept())

        if not _login(page, all_failures):
            browser.close()
            return 1

        for name, scenario in SCENARIOS:
            print(f"-- {name}")
            all_failures.extend(scenario(page))

        browser.close()

    if all_failures:
        print("browser regression FAILED:", file=sys.stderr)
        for failure in all_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("browser regression ok: all scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
