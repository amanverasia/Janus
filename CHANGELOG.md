# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
