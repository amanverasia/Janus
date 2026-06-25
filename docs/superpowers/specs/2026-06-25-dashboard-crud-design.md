# Phase 9: Full Dashboard CRUD

## Goal

Upgrade the Janus dashboard from mostly read-only to full CLI parity. Users can add/edit/remove providers, combos, token savers, pricing overrides, and server settings entirely from the web UI after `janus serve` — no config file editing required.

## Decisions

- **Config storage:** Move from YAML to SQLite. Providers, combos, settings, and pricing overrides become DB tables. YAML is a one-time seed file on first startup, then DB is source of truth.
- **Frontend stack:** HTMX + Jinja2 + Tailwind CDN (no React, no npm, no build step).
- **Sidebar:** Grouped into 4 sections (Monitor, Manage, Access, System) with 13 pages total.
- **Provider UX:** Pre-built catalog (~15 known providers) + custom provider option. Rich card grid with toggle, edit, test-connection, delete.
- **Combo editor:** Drag-and-drop model reordering via Sortable.js (CDN).
- **What stays in YAML:** `api_keys` (static list, bootstrap concern), `pricing` builtin overrides seed.
- **What moves to DB:** providers, combos, token saver settings, server settings, pricing overrides.

## Architecture: Config Migration (YAML → SQLite)

### New DB Tables

```sql
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT,
    models TEXT NOT NULL DEFAULT '[]',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    models TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_overrides (
    model TEXT PRIMARY KEY,
    input_per_mtok REAL NOT NULL,
    output_per_mtok REAL NOT NULL,
    cache_creation_per_mtok REAL NOT NULL DEFAULT 0.0,
    cache_read_per_mtok REAL NOT NULL DEFAULT 0.0
);
```

### Migration Behavior

1. On startup, `init_db()` creates the new tables idempotently.
2. `_seed_from_yaml(db_path, config)` runs after `init_db()`. For each table:
   - If table is empty AND config has entries → seed from YAML (one-time import)
   - If table already has data → skip (DB is source of truth)
3. `api_keys` (static list) stays in YAML only — bootstrap auth concern.
4. `pricing` YAML overrides seed into `pricing_overrides` table on first run, then DB is source of truth.
5. Existing `config.yaml` is never modified or deleted. It remains as a backup/reference.

### Runtime Changes

- `app.py:create_app()` loads providers/combos from DB instead of config object after seeding.
- `ProviderRegistry` is populated from DB `providers` table (only `is_enabled=1`).
- `SaverPipeline` reads enabled flags from `settings` table.
- `PricingRegistry` merges: builtin → DB `pricing_overrides` table (YAML no longer read at runtime after seed).
- `config.yaml` is still loaded for `api_keys`, and for initial seeding.

### Hot Reload

After dashboard CRUD operations, in-memory state must refresh:

- `_reload_providers(app)` — reads enabled providers from DB, builds new `Provider` objects (new `httpx.AsyncClient` for new/edited), closes clients for deleted/disabled providers, updates `app.state.providers` dict, rebuilds `ProviderRegistry`, rebuilds `FallbackHandler`.
- `_reload_combos(app)` — reads combos from DB, rebuilds combo registry.
- `_reload_savers(app)` — reads settings from DB, rebuilds `SaverPipeline`.
- `_reload_pricing(app)` — reads `pricing_overrides` from DB, rebuilds `PricingRegistry`.

## Dashboard Pages & Navigation

### Sidebar (4 grouped sections, 13 pages)

```
MONITOR
  Overview         /dashboard                 (existing, minor polish)
  Usage            /dashboard/usage           (existing)
  Analytics        /dashboard/analytics       (existing)

MANAGE
  Providers        /dashboard/providers       (CRUD + catalog — major upgrade)
  Combos           /dashboard/combos          (CRUD + drag-drop — major upgrade)
  Token Savers     /dashboard/savers          (NEW — toggle switches)
  Budgets          /dashboard/budgets         (existing CRUD)

ACCESS
  API Keys         /dashboard/keys            (existing CRUD)
  Tool Setup       /dashboard/tools           (NEW — copy-paste CLI config)

SYSTEM
  Pricing          /dashboard/pricing         (NEW — table + add/edit overrides)
  Settings         /dashboard/settings        (NEW — server settings form + export)
```

### Provider Management (CRUD + Catalog)

**Provider catalog** (`src/janus/dashboard/catalog.py`):

Pre-built templates for ~15 known providers. Each entry includes: `name`, `prefix`, `api_type`, `base_url`, `default_models`, `icon` (emoji or CSS class). Also includes a `"custom"` option (blank form).

Known providers in catalog: OpenAI, Anthropic, Google Gemini, Groq, Together AI, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI (Grok), Qwen/DashScope, OpenCode Zen.

**Add flow (HTMX modal):**
1. User clicks "Add Provider" → modal opens with catalog gallery (grid of provider names/icons)
2. User picks a provider → form pre-fills `api_type`, `base_url`, `prefix`, `default_models` (read-only). User enters: `id`, `api_key`, optionally edits models list.
3. Submit → `POST /dashboard/api/providers` → writes to DB → `_reload_providers(app)` → returns updated card partial.

**Edit flow:**
- Click "Edit" on a card → modal with all fields editable (api_key masked, models editable as comma-separated).
- Submit → `PUT /dashboard/api/providers/{id}` → updates DB → `_reload_providers(app)` → returns updated card partial.

**Toggle on/off:**
- Card has a switch → `PATCH /dashboard/api/providers/{id}/toggle` → sets `is_enabled` in DB → `_reload_providers(app)` → returns updated card partial. Disabled providers are filtered out of `ProviderRegistry` and visually dimmed.

**Test connection:**
- Click "Test Connection" → `POST /dashboard/api/providers/{id}/test` → sends a minimal request (1 token, `max_tokens=1`) to the upstream → returns latency + status badge. Uses the provider's `call()` method directly.

**Delete:**
- Click "Delete" → confirm modal → `DELETE /dashboard/api/providers/{id}` → removes from DB → closes `httpx.AsyncClient` → `_reload_providers(app)` → returns updated card partial.

### Combo Editor (CRUD + Drag-Drop)

- Create: modal form with combo name + model list builder. "Add model" dropdown filters to available `prefix/model` from enabled providers. Drag handles (Sortable.js via CDN) to reorder.
- Edit: same modal, pre-filled. Models can be added/removed/reordered.
- Delete: confirm → removes from DB.
- Stored as JSON array of `prefix/model` strings in `combos` table.
- HTMX endpoints: `POST /dashboard/api/combos`, `PUT /dashboard/api/combos/{id}`, `DELETE /dashboard/api/combos/{id}`.
- Drag-drop reorder: Sortable.js `onEnd` callback POSTs new order to `PUT /dashboard/api/combos/{id}` with reordered models array.

### Token Savers Page

- 3 toggle cards (RTK, Caveman, Ponytail) with description of what each does.
- Ponytail card has a segmented control: Lite / Full / Ultra.
- Changes write to `settings` table immediately via `POST /dashboard/api/settings` with `{ key, value }`.
- Settings keys: `saver_rtk_enabled`, `saver_caveman_enabled`, `saver_ponytail_enabled`, `saver_ponytail_level`.
- `SaverPipeline` is rebuilt on toggle change via `_reload_savers(app)`.

### Tool Setup Page

- 4 cards: Claude Code, Codex, Cursor, Cline.
- Each card shows the exact env vars / commands to copy:
  - Claude Code: `export ANTHROPIC_BASE_URL=http://localhost:20128/v1`
  - Codex/Cursor: `export OPENAI_BASE_URL=http://localhost:20128/v1`
  - Cline: Base URL + API key fields to paste into settings UI
- "Copy" button on each code block (clipboard API).
- If `require_api_key` is on, cards also show `export OPENAI_API_KEY=sk-janus-...` with a link to create a key.

### Pricing Page

- Table of all builtin models (28) + any DB overrides, sorted by name.
- Columns: model, input/mtok, output/mtok, cache creation/mtok, cache read/mtok.
- "Add Override" button → modal with model name + 4 rate fields → writes to `pricing_overrides` table.
- Override rows highlighted with edit/delete actions.
- HTMX endpoints: `POST /dashboard/api/pricing`, `PUT /dashboard/api/pricing/{model}`, `DELETE /dashboard/api/pricing/{model}`.
- At runtime, `PricingRegistry` merges: builtin → DB overrides.

### Settings Page

- Server settings form: `require_api_key` toggle (writable), `host` and `port` (read-only — can't change while running), `data_dir` (read-only).
- "Export Config" button → downloads current DB state as YAML file (GET `/dashboard/api/export` with `Content-Disposition: attachment`).
- Danger zone: "Reset to Defaults" button (confirm modal) → clears `providers`, `combos`, `settings`, `pricing_overrides` tables → re-seeds from `config.yaml` → full reload.
- Settings keys: `server_require_api_key`.

## Data Flow

All CRUD operations follow the same pattern:

```
Dashboard form submit → POST/PUT/DELETE/PATCH /dashboard/api/{resource}
→ Write to SQLite table
→ Rebuild in-memory state (_reload_providers / _reload_combos / _reload_savers / _reload_pricing)
→ Return HTMX partial (updated list/card)
```

### Provider Registry Rebuild (`_reload_providers`)

1. Read all enabled providers from `providers` table.
2. For new/changed providers: build new `Provider` object with fresh `httpx.AsyncClient`.
3. For deleted/disabled providers: close their `httpx.AsyncClient`.
4. Update `app.state.providers` dict.
5. Rebuild `ProviderRegistry` and `FallbackHandler`.

## Error Handling

- DB write fails → toast notification "Failed to save", partial returns error state.
- Provider build fails (bad api_type) → validation error in modal, no DB write.
- Test connection fails → red badge with error code, no state change.
- Duplicate prefix/id → DB constraint error caught, shown as form validation error.

## Settings Table Pattern

- Simple key-value: `settings(key TEXT PRIMARY KEY, value TEXT)`.
- Token savers stored as: `saver_rtk_enabled=true`, `saver_caveman_enabled=false`, etc.
- Server settings: `server_require_api_key=false`.
- Scalar values serialized as strings, parsed on read.
- Helper functions: `get_setting(key, default)`, `set_setting(key, value)`, `get_all_settings()`.

## API Key Auth for Dashboard

The dashboard inherits the existing `require_api_key` gate. If enabled, dashboard routes also require auth. For now, dashboard is local-only (127.0.0.1) so no extra auth layer. Browser session/cookie auth is a future concern, not this phase.

## Testing

### New Test Fixtures

- DB seed helpers for providers/combos/settings/pricing tables (extend existing `tests/fixtures/`).
- Catalog fixture (sample provider catalog entries).

### Test Layers

1. **Storage tests** (`tests/unit/storage/`):
   - `test_providers_db.py` — CRUD operations on providers table, idempotent migration, seed-from-YAML on first run.
   - `test_combos_db.py` — CRUD on combos table.
   - `test_settings_db.py` — key-value get/set, type coercion.
   - `test_pricing_db.py` — pricing override CRUD.

2. **Dashboard route tests** (`tests/integration/test_dashboard_crud.py`):
   - HTMX endpoints for each CRUD operation (POST/PUT/DELETE/PATCH).
   - Verify partials return correct HTML.
   - Verify provider toggle actually filters from registry.
   - Verify combo reorder persists correct order.
   - Verify test-connection endpoint (mocked httpx via respx).
   - Verify settings toggle rebuilds SaverPipeline.
   - Follow existing `_ensure_db` lazy init pattern.

3. **Migration tests** (`tests/integration/test_yaml_migration.py`):
   - Fresh DB with YAML config → providers/combos/settings seeded correctly.
   - DB with existing data → YAML ignored.
   - Export config → valid YAML output.

4. **Existing tests:** All 180 tests must still pass. Tests that call `create_app(config=...)` with inline configs need the seed step — `create_app()` will seed DB from the provided config if DB tables are empty.

## No New Dependencies

- Sortable.js via CDN (drag-drop, no npm).
- Clipboard API (built into browsers).
- Everything else uses existing HTMX + Jinja2 + Tailwind CDN.

## Scope Boundaries

- No React/SPA rewrite.
- No OAuth provider connections (API-key-only providers).
- No cloud sync or remote access.
- No login/JWT auth on the dashboard (local-only).
- No request logging/playground feature.
- No MITM proxy.
- `api_keys` static list stays in YAML (bootstrap concern).
- `host` and `port` are read-only in settings (can't change while server is running).
