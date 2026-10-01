# "Models you can't reach" view (#238)

Date: 2026-10-01. Status: draft for review. Issue: #238 (tracked in #239).

## Goal

Show operators which known models Janus cannot currently route, why, and which credential would
unlock them, with a one-click path to Connect. Success: on a fresh install with only an OpenAI
key, the new "Can't reach" tab lists the Anthropic/Gemini/DeepSeek/... catalog models grouped
by provider with `no_provider` and a Connect button that opens Connect with that provider
preselected; models of a configured-but-disabled provider show `provider_disabled`; a model whose
only account is cooled down appears under "Reachable soon", never as unreachable.

## Decisions (user-approved)

- Known-model sources: catalog `default_models`, inventory-discovered models, and models on
  provider rows. The synced pricing catalog (~3,700 LiteLLM/OpenRouter rows) is **excluded**
  because its names do not map reliably to a Connect provider.
- New Routing-hub tab rather than a sub-view inside the 667-line `ModelsPage.svelte`.
- `not_in_allowlist` is **omitted**: dashboard state is an operator surface and no other state
  section (including `models`) filters by the viewing key's allowlist, so adding it here alone
  would be inconsistent.

## Current behavior (verified in code)

- `ProviderRegistry.has_route(model_str)` (`providers/registry.py`) is the indexed boolean route
  check. `lookup()` materializes `ResolvedTarget` lists and must not be used in bulk (#102).
- `ProviderRegistry.providers` maps prefix → enabled `ProviderConfig` list (inventory accounts
  already expanded by `expand_gateway_provider`); disabled provider rows and rows without
  routable keys contribute no configs.
- `src/janus/catalog.py::PROVIDERS` entries have a `gateway` block (`id`, `prefix`,
  `default_models`) and/or an `inventory` block. `models/catalog.py::_inventory_routes()` maps
  inventory id → `(gateway provider id, prefix)`.
- `storage/upstream_models.py::list_distinct_discovered_models` returns discovered
  `(provider_id, model_id)` rows for inventory keys.
- `storage/providers_db.py::list_providers` returns provider rows (`id`, `prefix`, `models`,
  `is_enabled`, `catalog_id`, ...).
- `FallbackHandler.is_available(account_id, model)` reports cooldown state; cooldowns change
  over time, so they cannot be cached with the static part.
- The dashboard has a provider snapshot (`app.state.provider_snapshot`) that is replaced on every
  `reload_providers`.
- Connect (`ConnectPage.svelte`) has a provider selector (`providerId`, default `'auto'`) whose
  options are inventory provider ids.

## Design

### 1. Reachability core: `src/janus/routing/reachability.py`

Pure functions over plain inputs so they are unit-testable without an app.

```python
class UnreachableReason(StrEnum):
    NO_PROVIDER = "no_provider"
    PROVIDER_DISABLED = "provider_disabled"
    NO_ACTIVE_CREDENTIAL = "no_active_credential"
    MODEL_NOT_ENABLED = "model_not_enabled"

@dataclass(frozen=True)
class KnownModel:
    model: str              # bare model id, e.g. "claude-sonnet-4-5"
    prefix: str             # gateway prefix, e.g. "anthropic"
    catalog_id: str         # PROVIDERS key, e.g. "anthropic", "google"
    source: str             # "catalog" | "discovered" | "configured"

@dataclass(frozen=True)
class UnreachableModel:
    model: str
    prefix: str
    catalog_id: str
    reason: UnreachableReason
    source: str

@dataclass(frozen=True)
class ReachabilityReport:
    unreachable: list[UnreachableModel]   # sorted by (prefix, model)
    reachable: list[KnownModel]           # needed for the live "soon" pass
```

- `collect_known_models(provider_rows, discovered_rows) -> list[KnownModel]`: union of
  (a) every `PROVIDERS` gateway block's `default_models` under its `prefix`; (b) discovered rows
  mapped via `_inventory_routes()` (an inventory id with no gateway route is skipped);
  (c) each provider row's `models` under its `prefix` (catalog id from `catalog_id`, else the
  matching gateway id, else the prefix). Deduplicated on `(prefix, model)`; first source wins in
  the order catalog → configured → discovered. Entries with an empty prefix (`custom`) are
  skipped.
- `classify(known, registry, provider_rows) -> ReachabilityReport`: for each known model,
  `has_route(f"{prefix}/{model}")` true → reachable. Otherwise the reason is:
  - no provider row with this prefix → `no_provider`
  - rows exist, none enabled → `provider_disabled`
  - an enabled row exists but `registry.providers.get(prefix)` is empty → `no_active_credential`
  - otherwise → `model_not_enabled`
- `cooled_down(reachable, registry, handler) -> list[KnownModel]`: a reachable model whose every
  config under `registry.providers[prefix]` has `handler.is_available(config account id, model)`
  false. Uses the same account id the router uses (`upstream_key_id or id`, as in
  `registry.py`). Probed-headroom, rate-limit and quota demotions are not consulted.

### 2. Cache

`ReachabilityReport` is cached on the provider snapshot identity: a module-level
`weakref.WeakKeyDictionary[ProviderSnapshot, ReachabilityReport]` (or a dict keyed by
`id(snapshot)` holding the snapshot to avoid reuse) plus a single-flight lock. A new snapshot
after `reload_providers` naturally misses. Provider-row and discovered-model reads happen only on
a miss. `cooled_down` runs live on every request (bounded by the reachable count).

### 3. State section `models-unreachable` (`dashboard/api_v2.py`)

Query params (validated; invalid → `_invalid_query` 422): `provider` (prefix, ≤100 chars,
must be empty or a prefix present in the report), `reason` (empty or an `UnreachableReason`
value), `search` (≤200, case-insensitive substring of model id), `limit` (1–200, default
`DEFAULT_PAGE_SIZE`), `offset` (≥0).

Payload:

```json
{
  "groups": [
    {
      "prefix": "anthropic",
      "catalog_id": "anthropic",
      "name": "Anthropic",
      "count": 5,
      "reasons": {"no_provider": 5},
      "sample_models": ["claude-..."],
      "connect": {"kind": "connect", "href": "/dashboard/ui/connect?provider=anthropic"}
    }
  ],
  "models": [{"model": "...", "prefix": "...", "catalog_id": "...", "reason": "...", "source": "..."}],
  "soon": [{"model": "...", "prefix": "..."}],
  "unreachable_total": 123,
  "reachable_total": 45
}
```

- `groups` are unfiltered (≤ one per provider), sorted by count desc then prefix;
  `sample_models` holds up to 5 ids.
- `connect.kind = "connect"` with `href` `/dashboard/ui/connect?provider=<inventory id>` when the
  catalog id has an `inventory` block in `PROVIDERS`; otherwise `kind = "providers"`, `href`
  `/dashboard/ui/providers`. The catalog id is already the unified `PROVIDERS` key, so
  `google`/`dashscope` link correctly without extra bridging.
- `models` is filtered then paginated; `meta.pagination` uses the same shape as `routing`/
  `pricing` (`total`, `limit`, `offset`, `page`, `total_pages`) and `meta.query` echoes filters.
- `soon` is capped at 100 entries (live).
- No credential material, masked keys, base URLs, or account ids appear in the payload.

`_SECTIONS` gains `models-unreachable`; it is classified in `SHAPE_SECTIONS`
(`test_dashboard_state_contracts.py`, `.shape.json` generated) and gets raw/gzip budgets in
`test_dashboard_state_size.py`.

### 4. UI

- `nav.ts`: Routing hub gains `{ label: "Can't reach", href: \`${UI}/models/unreachable\`, icon:
  'layers', section: 'models-unreachable', title: "Models you can't reach", keywords:
  'unreachable missing connect unlock' }` placed after `Models`.
- New `dashboard-ui/src/lib/pages/UnreachableModelsPage.svelte`, registered wherever pages are
  mapped to sections in `routes/+page.svelte`:
  - group cards: provider name, count, reason chips, sample models, a button/link to
    `connect.href` via `navigate` ("Connect <name>" or "Add provider").
  - filter bar (provider select from `groups`, reason select, search) feeding server-side query
    params; shared `Pagination.svelte` for `models`.
  - "Reachable soon" panel when `soon` is non-empty.
  - All dynamic values rendered as text (Svelte text interpolation only; no `{@html}`).
  - Uses the cached-state/`PageSkeleton` pattern of other pages.
- `contracts.ts`/`types.ts`: types for the section (shape-pinned, so no byte fixture).
- `ConnectPage.svelte`: on mount, read `provider` from `window.location.search`; if it matches a
  provider option id, set `providerId` to it; otherwise keep `'auto'`.

## Error handling

Reachability failures (DB read errors) log a warning and return an empty report so the page
renders "nothing to show" rather than 500. Invalid query values return 422 before any work.

## Testing

- Unit `tests/unit/routing/test_reachability.py`: known-model collection and dedupe order,
  each reason code, reachable models excluded, `custom` skipped, cooled-down detection (all
  accounts cooled → soon; one available → not soon; per-model vs `__all__` cooldown).
- Integration `tests/integration/test_unreachable_models.py` (ASGI): payload groups/models,
  filters and pagination, 422s for bad `reason`/`provider`, connect hrefs (inventory provider →
  connect, gateway-only → providers), disabled provider reason, cache reuse within a snapshot and
  refresh after `reload_providers`, no secret strings (`sk-`, base URLs) in the body.
- Contract shape signature + size budgets.
- Vitest: `UnreachableModelsPage.test.ts` (renders groups, text-only rendering of a
  `<img onerror>` model id, filter → query params); `ConnectPage.test.ts` gains a preselect test.
- `scripts/browser_regression.py`: visit the tab, assert a group card renders, follow a Connect
  link and assert the Connect page loads with the provider preselected.

## Acceptance

- [ ] Unreachable list via `has_route()` with reason codes, grouped by provider
- [ ] Cooled-down models shown as "Reachable soon", never as unreachable
- [ ] Each group links to Connect with the provider preselected (or to Providers when Connect
      cannot onboard it)
- [ ] Server-side filters + pagination, 422 on bad input, no secrets, contract and size gates
      updated
