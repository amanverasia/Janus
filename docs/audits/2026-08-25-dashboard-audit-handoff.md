# Dashboard Audit Fix Handoff

**Audit date:** 2026-08-25

**Repository:** `amanverasia/Janus`

**Runtime tested:** Docker Compose, dashboard on port 20128, real persisted data mounted from
`./janus-data/`

**Purpose:** Give an implementation agent enough evidence, design guidance, test scope, and
sequencing information to fix the dashboard audit findings without repeating the discovery work.

## Scope and constraints

The audit exercised all dashboard pages with production-like data. It inspected rendered DOM,
interactive controls, server logs, SQLite data, and the associated FastAPI/storage/template code.
No upstream credentials or dashboard login keys are included in this report or the GitHub issues.

Preserve these project constraints while implementing fixes:

- Use the SQLite database as the runtime source of truth.
- Keep dashboard route initialization through `_ensure_db(request)`.
- Preserve Jinja auto-escaping; do not introduce `|safe` for dynamic data.
- Never log, snapshot, or include upstream credentials in test failure output.
- Use `.venv/bin/python -m pytest`, not bare `pytest`.
- Keep the `formats/` and `providers/` packages separated through the canonical model.

## Findings and GitHub tracking

| Priority | Finding | GitHub issue |
| --- | --- | --- |
| P0 | Decrypted upstream credentials are embedded in dashboard HTML | [#89](https://github.com/amanverasia/Janus/issues/89) |
| P0 | Dashboard toast and live-feed code permit DOM XSS | [#90](https://github.com/amanverasia/Janus/issues/90) |
| P1 | Inventory checks create no-op status history rows | [#88](https://github.com/amanverasia/Janus/issues/88) |
| P1 | “Today's Spend” uses conflicting time-window definitions | [#91](https://github.com/amanverasia/Janus/issues/91) |
| P2 | Invalid budget form values can cause a 500 response | [#92](https://github.com/amanverasia/Janus/issues/92) |
| P2 | Recorded credit balances are omitted from history views | [#93](https://github.com/amanverasia/Janus/issues/93) |
| P2 | Dashboard frontend dependencies require public CDNs | [#94](https://github.com/amanverasia/Janus/issues/94) |

## Recommended implementation order

1. Fix #89 and #90 first. They are independent security boundaries and can be developed in
   parallel by agents with non-overlapping file ownership.
2. Fix #88 before #93. Credit-history semantics should be decided after status-history noise is
   stopped at the write site.
3. Fix #92 independently; it is small and low risk.
4. Fix #91 after choosing the product-level reporting timezone behavior. This decision affects
   analytics and budget enforcement, so avoid a template-only patch.
5. Fix #94 independently, but verify packaging and Docker images rather than testing only from a
   source checkout.

## Issue #89: decrypted credentials embedded in HTML

### Evidence

`get_best_upstream_keys()` in `src/janus/storage/inventory_overview.py` decrypts each selected key.
`inventory_best_keys_partial.html` then renders the full value in a hidden span. The key detail
partial follows the same pattern after `get_upstream_key_detail()` decrypts its database row.

Live DOM inspection confirmed that real 35-character and 73-character credentials were present
before the Reveal buttons were clicked. CSS hiding is not access control.

Relevant files:

- `src/janus/storage/inventory_overview.py`
- `src/janus/storage/upstream_keys.py`
- `src/janus/dashboard/inventory_routes.py`
- `src/janus/dashboard/templates/inventory_best_keys_partial.html`
- `src/janus/dashboard/templates/inventory_key_detail_partial.html`

### Proposed implementation

1. Change best-key queries to select only identifiers, masked values, ranking fields, and display
   metadata. Do not select or decrypt `key_value` for an overview page.
2. Remove full secret fields from ordinary key detail JSON and HTML responses. If an existing
   export endpoint intentionally returns a credential, keep that behavior explicit and do not reuse
   it for rendering.
3. Add a dedicated reveal endpoint such as:

   `POST /dashboard/api/inventory/keys/{key_id}/reveal`

4. Require the existing authenticated dashboard session and `can_login` policy. Prefer POST over
   GET to discourage prefetching and caching.
5. Return `Cache-Control: no-store` and a narrow JSON payload. Never include the credential in URL
   parameters, HTML attributes, logs, exception text, or analytics.
6. Fetch only after an explicit Reveal or Copy action. Clear the value from the DOM after a short
   timeout and when a detail modal closes.
7. Consider re-authentication or a short-lived reveal token if Janus later gains multi-user access.

### Required tests

- Initial inventory overview HTML contains only masked values.
- Key detail partial and ordinary detail JSON contain no full credential.
- Authorized reveal returns the correct value and `Cache-Control: no-store`.
- Unauthenticated and `can_login=0` requests are rejected.
- Reveal errors do not leak the credential.
- Browser/DOM test proves that no secret exists before an explicit reveal.

## Issue #90: DOM XSS in toast and live feed

### Evidence

`janusToast()` in `src/janus/dashboard/templates/base.html` concatenates `message` into
`innerHTML`. The overview live feed in `overview.html` concatenates `ev.model` and `ev.status` into
an HTML string. Model names originate in client request payloads and must be treated as untrusted.

### Proposed implementation

- Construct DOM nodes with `document.createElement()`.
- Assign untrusted strings through `textContent`.
- Use `append()`, `replaceChildren()`, or a document fragment for the live feed.
- Keep fixed class names and static markup in code; do not add a generic HTML-unescape path.
- Search all dashboard templates for other dynamic `innerHTML`, `insertAdjacentHTML`, or
  `outerHTML` assignments and classify their inputs before closing the issue.

### Required tests

Use a payload such as `<img src=x onerror="window.__janusXss = true">` as a model name and toast
message. Assert it appears literally, creates no image or event-handler node, and never sets the
sentinel value. Preserve toast timeout/close behavior and live-feed styling.

## Issue #88: no-op inventory status history

### Evidence

The audit database contained:

- 46,876 total `upstream_key_history` rows
- 43,526 rows where `previous_status = new_status`
- 3,350 real transitions

The newest 20 rows rendered on the overview were all `active → active` OpenRouter entries from the
same recheck. `check_upstream_key()` calls `record_upstream_key_history()` unconditionally after
updating the key.

### Proposed implementation

Guard the write at the end of `check_upstream_key()`:

```python
if previous_status != final_status:
    await record_upstream_key_history(...)
```

Do not skip the key update itself. Credits, metadata, discovered models, usability, health,
rotated credentials, and `last_checked_at` must still update on an unchanged status.

As defense in depth, activity queries may filter old no-op rows until operators perform cleanup.

### Existing-data cleanup

Cleanup is destructive and must not run automatically. Tell operators to stop concurrent inventory
checks, back up `janus.db`, verify the count, and then run:

```sql
SELECT COUNT(*)
FROM upstream_key_history
WHERE previous_status = new_status;

DELETE FROM upstream_key_history
WHERE previous_status = new_status;
```

Run `VACUUM` only as a separate maintenance decision because it requires additional disk space and
an exclusive operation. Never bundle it into startup migration.

### Required tests

- `active → active` creates no history row but updates check/credit fields.
- `invalid → active` creates one row.
- `active → invalid` creates one row.
- A meaningful first transition with no previous status remains recordable.
- Activity queries do not return legacy no-op rows before cleanup.

## Issue #93: credit history is not displayed

### Dependency

Coordinate this with #88. If no-op status rows are removed, decide explicitly whether credit-only
events should exist and how they are represented.

### Proposed implementation options

Minimum implementation: render the stored `credits_remaining` snapshot when present.

Preferred implementation: introduce explicit history event semantics, for example
`event_type = status_change | credit_change`, and store enough information to render previous and
new credit values or a delta. Avoid presenting a credit-only event as `active → active`.

Formatting must use provider billing metadata. Not every provider reports US dollars; prepaid
credits, token units, subscription plans, postpaid accounts, and null balances must not all receive
a `$` prefix.

### Required tests

- Null balances render as an em dash rather than zero.
- Currency and non-currency units use correct labels.
- Status changes and credit-only changes are visually distinct.
- Activity remains readable on narrow screens and inside the detail modal.

## Issue #91: inconsistent definition of “today”

### Evidence

The overview calls `get_spend_summary(days=1)`, which selects a rolling 24-hour interval with
`timestamp >= datetime('now', '-1 days')`. Budget status selects rows by a calendar date using
SQLite `localtime`. The Docker container audited in this environment runs in UTC, while the
operator timezone is Asia/Kolkata.

At one point in the audit the same database produced approximately:

- $245.61 for the rolling 24-hour overview value
- $1.81 for the local calendar date
- $0.01 for the UTC calendar date

The rolling number was labeled “Today's Spend.”

### Proposed implementation

1. Add or document a single reporting timezone setting using an IANA name, defaulting to UTC.
2. Compute calendar-day start/end boundaries in Python with `zoneinfo.ZoneInfo`.
3. Convert boundaries to UTC before querying timestamp columns.
4. Share the boundary helper across overview spend, budget display, budget enforcement, and any
   “today” analytics.
5. If rolling data is valuable, expose it separately as “Last 24 hours.”
6. Define how timezone changes affect an already-active daily budget before implementing the UI.

Avoid relying on container-local SQLite `localtime`; deployment timezone configuration is not a
stable product-level reporting contract.

### Required tests

- Boundaries around midnight in UTC and Asia/Kolkata.
- At least one DST transition timezone.
- Overview and global budget totals match for the same configured day.
- Per-key totals use the same range.
- Rolling 24-hour results remain separate if retained.

## Issue #92: invalid budget selection causes a 500

### Evidence

`create_budget()` in `src/janus/dashboard/routes.py` executes `int(key_select)` for every value
except the string `global`. Invalid input raises an uncaught `ValueError`. The route also needs
explicit range and key-existence validation.

### Proposed implementation

- Add a shared form-validation helper or typed form model.
- Accept only `global` or a positive integer key ID.
- Confirm the selected API key exists.
- Require `daily_limit > 0`.
- Enforce the intended range for `warn_pct`.
- Return an HTMX-compatible 400/422 fragment or response header that displays a toast.
- Keep the current budgets table unchanged on validation failure.

### Required tests

Cover global, valid ID, empty, non-numeric, negative, nonexistent ID, zero/negative limit, and
out-of-range warning percentages. No invalid case should return 500.

## Issue #94: runtime CDN dependencies

### Evidence

`base.html` loads `https://cdn.tailwindcss.com`, producing a production warning on every page.
Other templates use CDN-hosted HTMX, Chart.js, and D3. This conflicts with local-first,
air-gapped, and strict-CSP deployments.

### Proposed implementation

- Compile Tailwind during maintenance/release work with template content paths configured.
- Vendor pinned HTMX, Chart.js, D3, and related runtime assets into
  `src/janus/dashboard/static/`.
- Prefer checked-in build artifacts if requiring Node during Python installation would complicate
  distribution.
- Ensure Hatch includes the static assets in wheels and sdists.
- Ensure Docker images include and serve the same files.
- Add an asset-update script and version/checksum documentation.
- Add a Content Security Policy after inline-script requirements are understood or removed.

### Required tests

- Disable outbound network and load every dashboard page.
- Exercise HTMX forms and partial refreshes.
- Verify charts render.
- Inspect wheel/sdist contents and the built Docker image.
- Confirm the Tailwind production warning and third-party asset requests are gone.

## Suggested ownership for parallel implementation

Agents share the worktree, so assign non-overlapping ownership and do not revert unrelated edits:

- **Security agent:** #89; owns inventory credential queries/routes/templates and reveal tests.
- **Frontend security agent:** #90; owns `base.html`, `overview.html`, and DOM/browser tests.
- **Inventory history agent:** #88 and #93; owns key checker/history storage, queries, templates,
  and inventory tests.
- **Budget/analytics agent:** #91 and #92; owns time-boundary helpers, budgets/routes, analytics,
  and tests.
- **Assets agent:** #94; owns static assets, packaging configuration, Docker verification, and CSP
  preparation.

The inventory agents must coordinate before editing the shared inventory templates. The
budget/analytics work should not be split until the timezone product behavior is decided.

## Verification commands

Run focused tests while developing, then the full project gates:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src/janus/ tests/
.venv/bin/ruff format --check src/janus/ tests/
.venv/bin/mypy src/janus/
.venv/bin/mkdocs build --strict
```

For dashboard changes, also build and run the Docker image with a copied/synthetic database and
exercise the browser with outbound network disabled. Never use the real `janus-data/` credentials
in committed fixtures, screenshots, HTML snapshots, or logs.

## Audit areas that passed

All audited dashboard routes loaded without application errors: Overview, Usage, Analytics,
Leaderboard, Request Logs, Key Inventory, Providers, Combos, Routing, Token Savers, Budgets, API
Keys, Tool Setup, Pricing, and Settings. The Usage SSE stream worked. Combo, saver, and budget forms
rendered correctly. The existing native passthrough fixes were verified: token savers operate on the
post-saver canonical request, and passthrough usage is recorded.

The Best Keys partial polling every 60 seconds is intentional and is not a bug. The plaintext
credential warning observed in the audit reflects a deployment without
`INVENTORY_ENCRYPTION_KEY`; do not hide that warning. Improve deployment guidance separately if
needed.
