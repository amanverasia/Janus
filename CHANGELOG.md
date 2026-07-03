# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-07-03

### Added
- **Routing** dashboard page (`/dashboard/routing`) — visualizes provider account try-order, priority, credits, cooldowns, and combo fallback chains
- Inventory key **priority** column, sort, and editable priority in key detail modal

### Changed
- `require_api_key` defaults to **on** (config, CLI template, DB seed)
- Disabling Require API Key in Settings requires **two confirmation dialogs**

## [1.0.0] - 2026-07-03

First stable release. Janus is a local-first AI routing gateway with multi-provider
fallback, key inventory, analytics, budgets, and a full dashboard.

### Added
- Key Inventory import page documents expected JSON format (wrapped `keys` array or bare array, field reference table)

### Fixed
- Dashboard sidebar stays fixed while main content scrolls; Sign out is always visible

## [0.3.2] - 2026-07-03

### Added
- Docker entrypoint auto-fixes bind-mount permissions for `/home/janus/.janus` on startup

### Fixed
- Settings **Require API Key** control is now a toggle (same pattern as Token Savers) and persists correctly

### Changed
- Deployment docs: document Docker volume UID mismatch and manual `chown` fallback

## [0.3.1] - 2026-07-03

### Added
- Key Inventory bulk-add preview with auto-provision of routing providers when adding upstream keys
- Shared inventory tab navigation across all Key Inventory pages
- Token Savers partial polling so toggle state syncs across devices/tabs
- DeepSeek/Moonshot credit balances normalized from CNY to USD (`INVENTORY_CNY_USD_RATE`, default 0.138)
- Analytics per-client API key labels in the breakdown (stores `client_key_label` on usage rows)

### Fixed
- Token saver toggles not persisting — HTMX now sends the correct checked value on save
- Token saver toggle visuals (plain CSS instead of broken Tailwind `peer-checked` on CDN)
- Providers dashboard 500 when inventory key stats were added to the template
- Provider edit/test/fetch-models uses inventory keys when the provider has no direct API key
- Analytics key breakdown showing `—` for named client keys
- Tool Setup copy button alignment on `/dashboard/tools`

### Changed
- `janus serve --host` default remains `127.0.0.1`; bind `0.0.0.0` explicitly for LAN access
- Saver settings use DB defaults via `ensure_saver_defaults()` (RTK on by default when unset)

## [0.3.0] - 2026-07-03

### Added
- Analytics **Details** view with a Sankey request-flow diagram (API Key → Model → Provider) and a Tokens/Cost/Requests metric toggle
- Costs↔Tokens toggle on the analytics spend chart, plus finer time ranges (24h/7D/30D/60D/90D)
- `scripts/seed_openrouter_pricing.py` — seeds per-model pricing overrides from the OpenRouter catalog
- Gemini inbound client endpoint — Gemini-native tools can now talk to Janus directly via `POST /v1beta/models/{prefix/model}:generateContent` (and `:streamGenerateContent`)
- Dashboard Test-Connection button — probes an upstream provider with a 1-token request and reports status + latency
- Dashboard Export Config — download the current DB state (providers, combos, pricing) as a YAML file
- Dashboard Reset-to-Defaults — clear DB tables and re-seed from `config.yaml` (danger zone on Settings page)
- Cooldown state now persists across server restarts via SQLite
- API key auth now accepts Gemini-style `x-goog-api-key` header and `?key=` query param
- Docker image published to `ghcr.io/amanverasia/janus` via CI (multi-arch: amd64 + arm64)

### Fixed
- Cost was recorded as $0 for vendor-prefixed models — `PricingRegistry` now matches names like `openai/gpt-4o-mini` against builtin/override keys
- Startup crash when migrating `upstream_keys` rows (`init_db` read tuple rows by string key)

### Changed
- Dockerfile copies `README.md` and `LICENSE` so the hatchling build succeeds in CI
- Dashboard mutations no longer wipe in-memory fallback cooldowns

## [0.2.2] - 2026-06-25

### Fixed
- Provider logos now visible on dark background (white variant + CSS filter)
- API key preserved when editing a provider (leave blank to keep current key)

## [0.2.1] - 2026-06-25

### Added
- Fetch Models button in provider Add/Edit modals — auto-populates models from upstream API
- Official provider logos via Simple Icons CDN in catalog gallery and provider cards
- Root URL `/` now redirects to `/dashboard`

## [0.2.0] - 2026-06-25

### Added
- Full dashboard CRUD: providers, combos, token savers, pricing overrides, and settings now manageable from the web UI
- Provider catalog with 14 known providers (OpenAI, Anthropic, Gemini, Groq, DeepSeek, OpenRouter, etc.) — add providers with just an API key
- DB-driven config: providers, combos, savers, and pricing stored in SQLite (YAML config file is now a one-time seed)
- Hot-reload: dashboard changes take effect immediately without server restart
- Combo editor with drag-and-drop model reordering (Sortable.js)
- Token savers toggle page (RTK, Caveman, Ponytail) with Ponytail level selector
- CLI Tool Setup page: copy-paste env vars for Claude Code, Codex, Cursor, Cline
- Pricing page: view builtin model prices + add/edit/delete custom overrides
- Settings page: toggle require_api_key from UI, view server config
- Grouped sidebar navigation (Monitor / Manage / Access / System) with 13 pages
- GPLv3 license, PyPI packaging (OIDC trusted publisher), MkDocs Material docs site

### Changed
- Config YAML is now a seed file — loaded once on first startup, then SQLite DB is source of truth
- `_build_provider()` is called during lifespan/reload, not in `create_app()`
- `_ensure_db()` now seeds config and reloads all state (for ASGITransport test compat)

## [0.1.0] - 2026-06-25

### Added
- Core routing gateway with canonical intermediate model translation
- Three format adapters: OpenAI, Anthropic, Gemini
- Four provider executors: openai_compat, anthropic, gemini, opencode_free
- SSE streaming translation between all supported formats
- Multi-account fallback routing with cooldowns (429→60s, 5xx→30s, auth→300s, network→15s)
- Named combos: ordered model sequences for automatic fallback chains
- Token savers: RTK compression (default ON), Caveman terse prompt, Ponytail lazy-dev prompt (3 levels)
- SQLite persistence: API keys (SHA256-hashed), usage tracking, budget storage
- Pricing engine: 28 builtin model prices, YAML-overridable, progressive prefix matching
- Budget enforcement: per-key and global daily limits with warn (80%) and block (100%) thresholds
- Analytics: cost tracking, spend trends, success rates, per-model/provider/key breakdowns
- HTMX dashboard with 7 pages: Overview, Providers, Combos, API Keys, Usage, Analytics, Budgets
- CLI: serve, config-init, config-path, keys (create/list/revoke), usage (stats/cost/by-key), budgets (list/set/delete), pricing (list/show)
- Docker support: multi-stage build, docker-compose with volume persistence
- GitHub Actions CI: ruff check + format, mypy --strict, pytest
- Connection pooling: shared httpx.AsyncClient per provider (100 connections, 20 keepalive)
- Provider caching in app.state.providers keyed by config.id
