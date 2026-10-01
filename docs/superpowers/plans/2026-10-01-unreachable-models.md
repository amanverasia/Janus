# "Models you can't reach" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dashboard tab listing known-but-unroutable models with reason codes, grouped by
provider, each group linking to Connect (provider preselected) or Providers.

**Architecture:** Pure reachability functions in `routing/reachability.py` classify known models
(catalog defaults + discovered + configured) with `ProviderRegistry.has_route()`. A new
`models-unreachable` state section caches the report per provider snapshot and adds a live
cooldown pass. A new Svelte page in the Routing hub renders it; Connect learns `?provider=`.

**Tech Stack:** Python 3.11 / FastAPI / aiosqlite; Svelte + TypeScript + vitest; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-01-unreachable-models-design.md`

## Global Constraints

- Worktree root: `/home/amanverasia/Projects/personal_projects/development/Janus-wt-238-unreachable`.
  Use `.venv/bin/python -m <tool>`; if `.venv` is missing, `python3.11 -m venv .venv &&
  .venv/bin/pip install -e ".[dev]"`. For the UI, run `npm ci` inside the worktree's
  `dashboard-ui/` — never symlink `node_modules` from another checkout (the build script's
  `npm ci` wipes the symlink target).
- Never call `ProviderRegistry.lookup()` for reachability; use `has_route()`.
- Reason values are exactly `no_provider`, `provider_disabled`, `no_active_credential`,
  `model_not_enabled`. No `not_in_allowlist`.
- Cooled-down models appear only in `soon` (cap 100), never in `models`/`groups`.
- Payload contains only model ids, prefixes, catalog ids, provider display names, reason codes,
  counts and dashboard hrefs. No account ids, key material, masked keys, or base URLs.
- Invalid `reason`/`provider` → 422 via `_invalid_query`.
- Svelte: text interpolation only, no `{@html}`.
- ruff (line 100), `mypy --strict`, `StrEnum`, `X | Y`, no code comments.
- Commit per task; messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The committed dashboard bundle (`src/janus/dashboard/static/app/`) must be rebuilt with
  `.venv/bin/python scripts/build_dashboard_ui.py` whenever `dashboard-ui/` changes, and
  `scripts/build_dashboard_ui.py --check` must pass.

## Review Focus

1. A provider row whose prefix maps to a catalog entry with an empty `default_models` and no
   configured/discovered models → produces no group (no empty cards). (Task 1
   `test_provider_without_models_produces_nothing`.)
2. The same model known from catalog and discovery (e.g. `openai/gpt-4o`) → one row, not two.
   (Task 1 `test_dedupe_prefers_catalog_source`.)
3. A disabled provider row plus an enabled row with the same prefix → classified by the enabled
   row (`no_active_credential` / `model_not_enabled`), not `provider_disabled`. (Task 1
   `test_mixed_enabled_disabled_rows`.)
4. Two dashboard requests in the same snapshot hit the DB once; after `reload_providers` the
   report reflects the new provider rows. (Task 2 `test_report_cached_per_snapshot`.)
5. `offset` beyond the filtered total → empty `models` page with correct totals, not 500.
   (Task 2 `test_offset_past_end`.)

---

### Task 1: Reachability core

**Files:**
- Create: `src/janus/routing/reachability.py`
- Test: `tests/unit/routing/test_reachability.py`

**Interfaces:**
- Produces (exact):

```python
class UnreachableReason(StrEnum):
    NO_PROVIDER = "no_provider"
    PROVIDER_DISABLED = "provider_disabled"
    NO_ACTIVE_CREDENTIAL = "no_active_credential"
    MODEL_NOT_ENABLED = "model_not_enabled"

@dataclass(frozen=True)
class KnownModel:
    model: str
    prefix: str
    catalog_id: str
    source: str

@dataclass(frozen=True)
class UnreachableModel:
    model: str
    prefix: str
    catalog_id: str
    reason: UnreachableReason
    source: str

@dataclass(frozen=True)
class ReachabilityReport:
    unreachable: list[UnreachableModel]
    reachable: list[KnownModel]

def collect_known_models(
    provider_rows: Sequence[Mapping[str, Any]],
    discovered_rows: Sequence[Mapping[str, Any]],
) -> list[KnownModel]: ...

def classify(
    known: Sequence[KnownModel],
    registry: ProviderRegistry,
    provider_rows: Sequence[Mapping[str, Any]],
) -> ReachabilityReport: ...

def cooled_down(
    reachable: Sequence[KnownModel],
    registry: ProviderRegistry,
    handler: FallbackHandler,
    *,
    limit: int = 100,
) -> list[KnownModel]: ...

def connect_target(catalog_id: str) -> dict[str, str]: ...
```

Rules:
- `collect_known_models`, in this source order (first occurrence of `(prefix, model)` wins):
  1. `catalog`: for each `catalog_id, entry` in `janus.catalog.PROVIDERS` with a dict
     `gateway` block and a non-empty `prefix`, each string in `gateway.get("default_models", [])`.
  2. `configured`: for each provider row with non-empty `prefix`, each model in its `models`
     (row value may be a JSON string or a list — reuse the same parsing the code base uses; see
     `models/catalog.py::_string_list`). Catalog id: row `catalog_id` if set, else the
     `PROVIDERS` key whose gateway `prefix` equals the row prefix, else the prefix.
  3. `discovered`: each row from `list_distinct_discovered_models` — map `provider_id` (an
     inventory id) via `PROVIDERS[provider_id]["gateway"]` → `prefix` (skip when no gateway
     block); catalog id is the inventory id.
  Models are stored bare (strip a leading `"{prefix}/"` if present). Result sorted by
  `(prefix, model)`.
- `classify`: per known model, `registry.has_route(f"{k.prefix}/{k.model}")` → reachable.
  Else, with `rows = [r for r in provider_rows if r["prefix"] == k.prefix]` and
  `enabled = [r for r in rows if truthy(r.get("is_enabled", 1))]`:
  no `rows` → `NO_PROVIDER`; no `enabled` → `PROVIDER_DISABLED`;
  `not registry.providers.get(k.prefix)` → `NO_ACTIVE_CREDENTIAL`; else `MODEL_NOT_ENABLED`.
  Note `has_route` resolves `PREFIX_ALIASES`; check whether row prefixes are already canonical
  and, if `registry.providers` is keyed by the aliased prefix, use the same alias mapping
  (`PREFIX_ALIASES` in `providers/registry.py`) for the `registry.providers.get` check.
- `cooled_down`: for each reachable model, `configs = registry.providers.get(prefix, [])`;
  account id per config is `config.upstream_key_id or config.id`; include the model when
  `configs` is non-empty and `handler.is_available(account_id, k.model)` is False for every
  config. Stop at `limit`.
- `connect_target(catalog_id)`: if `PROVIDERS.get(catalog_id, {})` has a dict `inventory` block →
  `{"kind": "connect", "href": f"/dashboard/ui/connect?provider={quote(catalog_id)}"}`; else
  `{"kind": "providers", "href": "/dashboard/ui/providers"}`.

- [ ] **Step 1: Write failing tests** (`tests/unit/routing/test_reachability.py`)

Build real `ProviderRegistry` instances the way existing registry unit tests do (read
`tests/unit/` for `ProviderRegistry` construction / `register` usage and mirror it; do not mock
`has_route`). For `cooled_down`, use a real `FallbackHandler` and `mark_cooldown` (see
`tests/unit/routing/` for construction). Tests to write (each asserts exact values):

```python
def test_catalog_defaults_unreachable_without_provider():
    # empty registry, no rows → every catalog default (e.g. anthropic's) is NO_PROVIDER,
    # catalog_id "anthropic", source "catalog"

def test_reachable_model_excluded():
    # registry with prefix "openai" serving "gpt-4o" → gpt-4o in report.reachable, not unreachable

def test_provider_disabled_reason():
    # row {"prefix": "anthropic", "is_enabled": 0} and empty registry → PROVIDER_DISABLED

def test_no_active_credential_reason():
    # enabled row for "anthropic", registry has no configs for it → NO_ACTIVE_CREDENTIAL

def test_model_not_enabled_reason():
    # registry serves openai/gpt-4o only; catalog default openai/o3 → MODEL_NOT_ENABLED

def test_mixed_enabled_disabled_rows():
    # one disabled + one enabled "anthropic" row, no registry configs → NO_ACTIVE_CREDENTIAL

def test_dedupe_prefers_catalog_source():
    # discovered row ("openai", "gpt-4o") + catalog default gpt-4o → one KnownModel, source "catalog"

def test_discovered_model_maps_inventory_id_to_prefix():
    # discovered {"provider_id": "google", "model_id": "gemini-x"} → prefix "gemini", catalog_id "google"

def test_configured_models_and_namespaced_strip():
    # row prefix "acme", models ["acme/m1", "m2"] → KnownModel m1 and m2 under "acme"

def test_custom_and_empty_prefix_skipped():
    # PROVIDERS["custom"] has prefix "" → contributes nothing

def test_provider_without_models_produces_nothing():
    # catalog entry with empty default_models and no rows → no KnownModel for that prefix

def test_cooled_down_all_accounts():
    # two accounts on "openai" serving gpt-4o, both mark_cooldown(..., model="gpt-4o") → in soon

def test_cooled_down_one_available_not_soon():
    # only one of two cooled → not in soon

def test_cooled_down_all_scope():
    # mark_cooldown without model (the "__all__" scope) also counts

def test_connect_target():
    assert connect_target("anthropic") == {
        "kind": "connect", "href": "/dashboard/ui/connect?provider=anthropic"}
    assert connect_target("cline")["kind"] == "providers"   # pick a gateway-only PROVIDERS key
```

Fill each body with real setup and asserts (the comments state the exact expectation). Pick a
gateway-only id by checking `PROVIDERS` for an entry with `gateway` but no `inventory`.

- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/unit/routing/test_reachability.py -v` →
  FAIL (module missing).
- [ ] **Step 3:** Implement `src/janus/routing/reachability.py` per the rules above.
- [ ] **Step 4:** Tests PASS; `ruff check`, `ruff format --check`, `mypy src/janus/` clean.
- [ ] **Step 5:** Commit `feat(routing): known-model reachability classifier (#238)`.

---

### Task 2: `models-unreachable` state section

**Files:**
- Modify: `src/janus/dashboard/api_v2.py` (`_SECTIONS`, new `_models_unreachable_data`,
  branch in `get_dashboard_state`, new `reason` query param)
- Create: `src/janus/dashboard/reachability_cache.py`
- Modify: `tests/integration/test_dashboard_state_contracts.py` (`SHAPE_SECTIONS`),
  generate `dashboard-ui/src/lib/contract-fixtures/models-unreachable.shape.json`
- Modify: `tests/integration/test_dashboard_state_size.py` (budgets)
- Test: `tests/integration/test_unreachable_models.py`

**Interfaces:**
- Consumes: Task 1 (`collect_known_models`, `classify`, `cooled_down`, `connect_target`,
  `UnreachableReason`).
- Produces: `async def get_reachability_report(app: FastAPI, snapshot: ProviderSnapshot) ->
  ReachabilityReport` in `dashboard/reachability_cache.py`; state section JSON per spec §3.

Cache (`reachability_cache.py`): module-level `_reports: dict[int, tuple[ProviderSnapshot,
ReachabilityReport]]` keyed by `id(snapshot)` (holding the snapshot reference so the id cannot
be reused while cached), keep at most 4 entries (drop oldest), and an `asyncio.Lock` for
single-flight. On miss: `list_providers(db_path)`, `list_distinct_discovered_models(db_path)`
(guard: the `upstream_models` table may not exist on old DBs — follow
`models/catalog.py::_table_exists`), then `collect_known_models` + `classify`. On any exception,
log warning and return `ReachabilityReport([], [])` (not cached).

Section builder:

```python
async def _models_unreachable_data(
    request: Request, *, provider: str, reason: str, search: str, limit: int, offset: int
) -> tuple[dict[str, Any], dict[str, Any]]:
```

- Snapshot: `request.app.state.provider_snapshot` (verify the attribute name; fall back to
  `ensure_provider_snapshot(request.app)` as other sections do).
- Validate: `reason` empty or in `UnreachableReason` values, else `_invalid_query("reason",
  "expected one of ...")`; `provider` empty or equal to some unreachable model's prefix, else
  `_invalid_query("provider", "unknown provider prefix")`.
- `groups`: per prefix over the **unfiltered** unreachable list: `prefix`, `catalog_id`,
  `name` (`PROVIDERS[catalog_id]["gateway"]["name"]` or `inventory.display_name`/`name`, else
  prefix), `count`, `reasons` (`{reason: count}`), `sample_models` (first 5), `connect`
  (`connect_target`). Sorted by `(-count, prefix)`.
- `models`: unreachable filtered by provider, reason, case-insensitive `search` substring on
  model; then `[offset: offset + limit]`; rows `{model, prefix, catalog_id, reason, source}`.
- `soon`: `[{model, prefix}]` from `cooled_down(report.reachable, snapshot.registry,
  snapshot.handler)`.
- `unreachable_total`, `reachable_total`.
- meta: `{"pagination": {total, limit, offset, page, total_pages}, "query": {"provider",
  "reason", "search"}}` with the same formulas as the `routing` branch.

`get_dashboard_state` gains `reason: str = Query("", max_length=40)` and a branch for
`section == "models-unreachable"` calling `_response(request, db_path, section, data, meta=meta)`.

- [ ] **Step 1: Write failing integration tests** (`tests/integration/test_unreachable_models.py`)

Follow the dashboard test pattern of `tests/integration/test_dashboard_api_v2.py` (app fixture,
`with_dashboard_auth` / `DASHBOARD_TEST_API_KEY` from `tests/fixtures/dashboard_auth.py`,
`_ensure_db`). Config: one enabled `openai_compat` provider prefix `openai` serving `gpt-4o`
and one disabled provider prefix `anthropic` (create it then toggle disabled through
`storage/providers_db.py`, then `reload_providers`). Tests:

- `test_section_lists_groups_and_reasons`: GET `/dashboard/api/v2/state/models-unreachable` →
  200; `data.groups` contains an `anthropic` group whose `reasons` is `{"provider_disabled": N}`
  (N = len of anthropic catalog defaults); an `openai` group with `model_not_enabled` for e.g.
  `o3`; `gpt-4o` absent from `data.models`; a `deepseek` group with `no_provider`.
- `test_connect_hrefs`: anthropic group `connect == {"kind": "connect", "href":
  "/dashboard/ui/connect?provider=anthropic"}`; a gateway-only provider group (if any has
  defaults) has `kind == "providers"`.
- `test_filters_and_pagination`: `?reason=no_provider&limit=2&offset=0` → 2 models all
  `no_provider`, `meta.pagination.total` equals the count of `no_provider` rows; `?provider=anthropic`
  only anthropic; `?search=claude` only ids containing "claude".
- `test_offset_past_end`: `?offset=100000` → 200, `models == []`, totals unchanged.
- `test_invalid_reason_422` and `test_invalid_provider_422`.
- `test_soon_lists_cooled_down`: mark the openai account cooled for `gpt-4o` via
  `app.state.provider_snapshot.handler.mark_cooldown(account_id, "rate_limit", model="gpt-4o")`
  → `data.soon == [{"model": "gpt-4o", "prefix": "openai"}]` and gpt-4o still not in `models`.
- `test_report_cached_per_snapshot`: monkeypatch `janus.dashboard.reachability_cache.list_providers`
  with a counting wrapper; two GETs → 1 call; enable anthropic + `reload_providers` → next GET
  calls again and anthropic reason changes from `provider_disabled`.
- `test_no_secrets_in_payload`: response text contains neither the provider api key string,
  nor its base URL, nor `"account_id"`.

- [ ] **Step 2:** Run → FAIL (404 section not found).
- [ ] **Step 3:** Implement cache module + section.
- [ ] **Step 4:** Contracts: add `"models-unreachable"` to `SHAPE_SECTIONS`; run
  `JANUS_REGEN_CONTRACT_FIXTURES=1 .venv/bin/python -m pytest tests/integration/test_dashboard_state_contracts.py`
  then again without the env var → PASS. Confirm only the new `.shape.json` was created/changed
  (`git status`).
- [ ] **Step 5:** Size budgets: add `"models-unreachable"` to `RAW_BUDGETS` (start at 40_000) and
  `GZIP_BUDGETS` (8_000); run `tests/integration/test_dashboard_state_size.py`, and tighten the
  budgets to roughly 2x the measured size (print the sizes once to measure).
- [ ] **Step 6:** Run new tests + `tests/integration/test_dashboard_state_contracts.py` +
  `tests/integration/test_dashboard_state_size.py` + `tests/integration/test_dashboard_api_v2.py`
  → PASS; ruff/format/mypy clean.
- [ ] **Step 7:** Commit `feat(dashboard): models-unreachable state section (#238)`.

---

### Task 3: "Can't reach" page

**Files:**
- Modify: `dashboard-ui/src/lib/nav.ts`
- Create: `dashboard-ui/src/lib/pages/UnreachableModelsPage.svelte`
- Create: `dashboard-ui/src/lib/pages/UnreachableModelsPage.test.ts`
- Modify: `dashboard-ui/src/routes/+page.svelte` (import + `{:else if active.section ===
  'models-unreachable'}` branch)
- Modify: `dashboard-ui/src/lib/contracts.ts` / `types.ts` only if shape-pinned sections there
  have typed entries (check how `routing`/`models` are typed and mirror it)
- Modify (if needed): server-side SPA route list for `/dashboard/ui/models/unreachable`
  (check `src/janus/dashboard/ui_routes.py`; if routes are enumerated, add it and a test in
  `tests/integration/test_dashboard_ui.py` that GET returns 200)
- Rebuild: `src/janus/dashboard/static/app/`

**Interfaces:**
- Consumes: payload of Task 2 (`groups`, `models`, `soon`, `unreachable_total`,
  `reachable_total`, `meta.pagination`, `meta.query`). The page can be written before Task 2
  lands; tests use literal fixture data.
- Props: `data: JsonObject`, `navigate: (href: string) => void`,
  `navigateQuery: (params: Record<string, string>) => void` (same types other pages use).

Behavior:
- nav: Routing hub, after `Models`: `{ label: "Can't reach", href: `${UI}/models/unreachable`,
  icon: 'layers', section: 'models-unreachable', title: "Models you can't reach", keywords:
  'unreachable missing connect unlock' }`. Verify `routeFor('/dashboard/ui/models/unreachable')`
  returns this tab, not `Models` (add a nav unit test if a nav test file exists).
- `PageHeader` with title "Models you can't reach" and a description: "Known models Janus can't
  route right now, and the credential that unlocks them."
- Summary `StatCard`s: unreachable total, reachable total, reachable soon count.
- Group cards (from `groups`): name, `count` models, reason chips with human labels
  (`no_provider` → "No provider", `provider_disabled` → "Provider disabled",
  `no_active_credential` → "No active credential", `model_not_enabled` → "Model not enabled"),
  sample models, and a button calling `navigate(group.connect.href)` labelled
  `Connect ${name}` when `kind === 'connect'` else `Add provider`.
- Filters: provider select (options from `groups` prefixes), reason select, search input; on
  change call `navigateQuery({ provider, reason, search, offset: '0' })` (match how other
  pages clear/omit empty params).
- Table of `models` (model, provider, reason label, source) + `<Pagination {data}
  {navigateQuery} label="models" />`.
- "Reachable soon" panel listing `soon` (`prefix/model`) with a note "Every account for these
  models is cooling down; they'll route again when the cooldown ends."
- `EmptyState` when `unreachable_total === 0`: "Every known model is reachable."
- Use `$lib/data` helpers (`firstList`, `object`, `text`, `number`) as other pages do.

- [ ] **Step 1: Write failing vitest** (`UnreachableModelsPage.test.ts`, pattern from
  `ModelsPage.test.ts`):
  - renders group name, count and reason chip label; clicking "Connect Anthropic" calls
    `navigate('/dashboard/ui/connect?provider=anthropic')`; a `kind: 'providers'` group shows
    "Add provider".
  - a model id `<img src=x onerror=alert(1)>` renders as literal text (`getByText`) and no `img`
    element exists in the container.
  - changing the reason select calls `navigateQuery` with `reason: 'no_provider'`.
  - empty state text when totals are 0; "Reachable soon" panel shows `openai/gpt-4o`.
- [ ] **Step 2:** `cd dashboard-ui && npm ci && npm run test -- UnreachableModelsPage` → FAIL.
- [ ] **Step 3:** Implement nav entry, page, `+page.svelte` branch, SPA route if needed.
- [ ] **Step 4:** `npm run check && npm run test` → PASS. Rebuild bundle:
  `.venv/bin/python scripts/build_dashboard_ui.py`, then
  `.venv/bin/python scripts/build_dashboard_ui.py --check` → PASS.
- [ ] **Step 5:** Commit page, test, nav, `+page.svelte`, and the rebuilt bundle:
  `feat(dashboard-ui): "Can't reach" models page (#238)`.

---

### Task 4: Connect preselects `?provider=`

**Files:**
- Modify: `dashboard-ui/src/lib/pages/ConnectPage.svelte`
- Modify: `dashboard-ui/src/lib/pages/ConnectPage.test.ts`
- Rebuild: `src/janus/dashboard/static/app/`

Behavior: when the provider option list becomes available, if
`new URLSearchParams(window.location.search).get('provider')` equals one of the option ids
(the same ids bound to `providerId`), set `providerId` to it exactly once (do not override a
later user choice). Unknown values leave `'auto'`.

- [ ] **Step 1: Failing tests** in `ConnectPage.test.ts`: set
  `window.history.replaceState({}, '', '/dashboard/ui/connect?provider=anthropic')` before render
  with provider options including `anthropic` → the provider `<select>` value is `anthropic`;
  with `?provider=nope` → stays `auto`. Reset the URL in `afterEach`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `npm run check && npm run test` PASS; rebuild bundle; `--check` PASS.
- [ ] **Step 5:** Commit `feat(dashboard-ui): Connect preselects provider from query (#238)`.

---

### Task 5: Browser regression, docs, full gates

**Files:**
- Modify: `scripts/browser_regression.py`
- Modify: `CHANGELOG.md`, `AGENTS.md`, and the dashboard docs page under `docs/` that lists
  dashboard tabs (find it with `grep -rl "Token savers" docs/`)

- [ ] **Step 1:** `browser_regression.py`: add `("/dashboard/ui/models/unreachable", "Models you
  can't reach")` (use the exact page heading) to `ROUTES`; add the tab to the Routing hub entry in
  the hub-tabs list; add `scenario_unreachable_connect_link(page)` that visits the page, and if a
  "Connect …" button exists clicks it and asserts the URL contains `/dashboard/ui/connect?provider=`
  and the provider select's value equals that provider (skip silently when the seeded data has
  no connect-kind group). Register the scenario in `run()` like the others.
- [ ] **Step 2:** Run it against a live server per `CONTRIBUTING.md` / the script docstring
  (`.venv/bin/janus serve` on a temp data dir with a seeded key, `JANUS_SMOKE_API_KEY` set,
  `playwright install chromium` if needed). All scenarios PASS.
- [ ] **Step 3:** Docs: CHANGELOG `### Added` entry:

```markdown
- **"Models you can't reach"** — a new Routing tab lists catalog, discovered, and
  configured models that Janus can't route, with the reason (no provider, provider
  disabled, no active credential, model not enabled), grouped by provider with a
  Connect button that opens Connect with that provider preselected. Models whose
  every account is cooling down are shown separately as "Reachable soon". (#238)
```

  AGENTS.md "Dashboard navigation and Connect": one bullet — "`models-unreachable`
  (`/dashboard/ui/models/unreachable`, Routing hub) is computed by
  `routing/reachability.py` via `has_route()` only, cached per provider snapshot in
  `dashboard/reachability_cache.py`; cooldowns are a live soft state (`soon`), never a reason.
  Connect preselects `?provider=<inventory id>`." Add the tab to the docs page.
- [ ] **Step 4:** Full gates, all must pass:

```bash
.venv/bin/python -m ruff check src/ tests/ scripts/
.venv/bin/python -m ruff format --check src/ tests/ scripts/
.venv/bin/python -m mypy src/janus/ scripts/
.venv/bin/python -m pytest --cov=janus --cov-fail-under=80 -q
.venv/bin/python scripts/build_dashboard_ui.py --check
.venv/bin/python scripts/migration_smoke.py
.venv/bin/mkdocs build --strict
```

- [ ] **Step 5:** Commit `docs: unreachable models view (#238)` (+ browser regression).
