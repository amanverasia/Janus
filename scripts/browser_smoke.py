#!/usr/bin/env python3
"""Browser smoke for the dashboard SPA (#122).

Drives a running Janus server with Playwright: signs in through the real
login form, then visits every dashboard route and asserts the SPA rendered
its page (panel content visible, not bounced back to sign-in) and that the
configured API key value never leaks into the DOM.

Environment:
    JANUS_SMOKE_BASE_URL  base URL of the running server
                          (default http://127.0.0.1:20131)
    JANUS_SMOKE_API_KEY   Janus API key with dashboard access (required)

Exit code 0 means every route rendered.
"""

from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("JANUS_SMOKE_BASE_URL", "http://127.0.0.1:20131")
API_KEY = os.environ.get("JANUS_SMOKE_API_KEY", "")

ROUTES = [
    ("/dashboard/ui", "Overview"),
    ("/dashboard/ui/usage", "Usage"),
    ("/dashboard/ui/analytics", "Analytics"),
    ("/dashboard/ui/leaderboard", "Leaderboard"),
    ("/dashboard/ui/request-logs", "Request logs"),
    ("/dashboard/ui/inventory", "Inventory"),
    ("/dashboard/ui/inventory/keys", "Inventory keys"),
    ("/dashboard/ui/inventory/add", "Add inventory"),
    ("/dashboard/ui/inventory/import", "Import inventory"),
    ("/dashboard/ui/providers", "Providers"),
    ("/dashboard/ui/models", "Models"),
    ("/dashboard/ui/combos", "Combos"),
    ("/dashboard/ui/routing", "Routing"),
    ("/dashboard/ui/savers", "Token savers"),
    ("/dashboard/ui/budgets", "Budgets"),
    ("/dashboard/ui/keys", "API keys"),
    ("/dashboard/ui/tools", "Tools"),
    ("/dashboard/ui/pricing", "Pricing"),
    ("/dashboard/ui/settings", "Settings"),
]

LOGIN_WAIT_MS = 15_000
ROUTE_WAIT_MS = 20_000


def smoke() -> int:
    if not API_KEY:
        print("JANUS_SMOKE_API_KEY is required", file=sys.stderr)
        return 2

    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        response = page.goto(f"{BASE_URL}/dashboard/login", wait_until="domcontentloaded")
        if response is None or not response.ok:
            failures.append(f"login page returned {response.status if response else 'no response'}")
        page.fill('input[name="api_key"]', API_KEY)
        page.click('button[type="submit"]')
        try:
            page.wait_for_url("**/dashboard/ui**", timeout=LOGIN_WAIT_MS)
        except PlaywrightTimeoutError:
            failures.append("login did not reach /dashboard/ui")
            browser.close()
            return 1

        for path, label in ROUTES:
            started = time.monotonic()
            try:
                response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
                page.wait_for_selector("main", state="visible", timeout=ROUTE_WAIT_MS)
                body = page.inner_text("body")
                if response is None or not response.ok:
                    failures.append(f"{path}: HTTP {response.status if response else 'none'}")
                elif "Sign in" in (page.title() or ""):
                    failures.append(f"{path}: bounced back to sign-in")
                elif API_KEY in body:
                    failures.append(f"{path}: API key leaked into the DOM")
                else:
                    elapsed = int((time.monotonic() - started) * 1000)
                    print(f"ok {path} ({label}) in {elapsed}ms")
            except Exception as exc:  # noqa: BLE001 - report every route failure
                failures.append(f"{path}: {type(exc).__name__}: {exc}")

        browser.close()

    if failures:
        print("browser smoke FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"browser smoke ok: {len(ROUTES)} routes rendered")
    return 0


if __name__ == "__main__":
    sys.exit(smoke())
