# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Fusion combo strategy** — combos can now run in `fusion` mode (9router port): the request fans out to all combo members in parallel (tools stripped, non-streaming, tool history flattened), a quorum + straggler-grace collector gathers answers, and a judge model (configurable via `combo_fusion_judge`, defaults to the first panel member) synthesizes one authoritative answer from anonymized `[Source N]` responses. The judge keeps the client's stream flag and tools and rides the normal fallback machinery. Panel usage is recorded per model. Judge is validated before any panel spend, with fallback to the first answering panel model. Tuning: `combo_fusion_min_panel`, `combo_fusion_straggler_grace_s`, `combo_fusion_hard_timeout_s`
- **Per-provider model allowlist** — new optional `allowed_models` on providers (dashboard field, comma-separated, `fnmatch` globs like `claude-opus-*` supported; empty = all models). Blocked models are rejected at routing time (400) and hidden from `/v1/models` and Ollama `/api/tags`. Lets you expose only selected models from a provider (e.g. only `claude-opus-4-7` from Anthropic)
- **Request log "User" column** — request logs now record and display which client API key made each call (`client_key_label` for config keys, `key #<id>` for DB-issued keys, "—" for anonymous)
- **Per-saver savings metrics** — the token-saver pipeline measures request size before/after each saver, logs shrinkage, and shows cumulative per-saver savings ("saved X KB across N requests, since restart") on the Token Savers page. Stats survive dashboard-triggered saver reloads
- **Caveman levels** — the Caveman saver now has `lite` / `full` / `ultra` levels (dashboard select, `saver_caveman_level`), with 9router's safety boundaries (security warnings, irreversible confirmations, and multi-step instructions are always written normally; code/paths/commands/errors/URLs never abbreviated)
- **Combo Routing settings UI** — Settings page section for `combo_strategy` (fallback / round_robin / fusion), `combo_sticky_limit`, and fusion tuning, with server-side validation (whitelisted strategy, finite bounded numbers)
- **503 + Retry-After on exhausted cooldowns** — when every account for a model is cooling down, Janus now returns `503` with a `Retry-After` header derived from the earliest cooldown expiry instead of a generic `400`
- **Body-text rate-limit detection** — upstream error bodies are scanned for rate-limit markers ("rate limit", "too many requests", "quota exceeded", "capacity", "overloaded", "resource exhausted"); a disguised rate limit (e.g. HTTP 400 with "quota exceeded") now cools the account down with rate-limit backoff and falls back to the next account. Handles dict, list, string, and other JSON body shapes
- **RTK filter upgrade** — ported 9router's compression filter set: git-log, git-status, grep (per-file cap, non-matching lines preserved), find (per-dir cap, path-only detection), tree, and build-output filters with priority auto-detection, plus line-based head+tail smart truncation (120 head / 60 tail above 250 lines). Compression gate raised to 500 bytes minimum, 10 MiB raw cap

- **Ollama-compatible endpoints** (`POST /api/chat`, `GET /api/tags`, `GET /api/version`) — tools that only speak Ollama can now route through Janus. Full format adapter with NDJSON streaming (Ollama defaults to streaming), tool-call round-trips (positional call-id assignment), `images`, `options` (`num_predict`/`temperature`/`top_p`/`stop`), and `thinking` passthrough. Phase 8.6 — completes the 9router feature-parity plan
- **Subscription quota tracking** — per-provider quota windows (`5h`, `daily`, `weekly`, `monthly`, all UTC) with a request or token limit, configurable in the provider Add/Edit forms. Provider cards show a usage bar + reset countdown; exhausted providers are deprioritized in fallback ordering (soft enforcement — never blocked). Counters are shared across a provider's inventory accounts and seeded from the `usage` table on startup/reload. Phase 8.5 of the 9router parity plan
- **GitHub Copilot OAuth provider** — first subscription/OAuth provider (Phase 8.4 of the 9router parity plan). New `api_type: github_copilot` with device-code login from the dashboard ("Connect GitHub Account" in Add Provider), automatic exchange of the long-lived GitHub OAuth token for short-lived Copilot session tokens (refreshed before expiry behind a single-flight lock), OpenAI-compatible routing (`copilot/gpt-4o`, ...), Fetch Models and Test Connection support. New dashboard endpoints `POST /dashboard/api/oauth/copilot/{start,poll}`
- **Headroom token saver** — optional integration with an external [Headroom](https://github.com/chopratejas/headroom) compression proxy: when enabled, conversations are sent through `POST {url}/v1/compress` before any other saver and before routing. Fail-open (Headroom being down never breaks a request). Toggle + URL field on the Token Savers dashboard page (`saver_headroom_enabled`, `saver_headroom_url`). Phase 8.3 of the 9router parity plan
- **Request logging / debug mode** — opt-in capture of full request/response bodies to SQLite (`request_logs` table, 64 KB body truncation, last 500 requests kept). New dashboard page `/dashboard/request-logs` with per-request JSON detail, export, and clear; toggle in Settings (`server_request_logging`, off by default). Streaming requests and all-providers-exhausted failures are captured too. Phase 8.2 of the 9router parity plan
- **OpenAI Responses API endpoint** (`POST /v1/responses`) — Codex CLI and other Responses-API clients now work natively. New bidirectional format adapter (`formats/openai_responses.py`) translates `input` items, flat tool definitions, `function_call`/`function_call_output` round-trips, `max_output_tokens`, `reasoning.effort`, and streaming via named SSE events (`response.created` … `response.completed`). Phase 8.1 of the 9router feature-parity plan (`docs/superpowers/specs/2026-07-05-phase8-9router-parity.md`)

### Fixed
- Combo round-robin rotation now honors `combo_sticky_limit` (stay on a combo member for N requests before advancing); previously the limit was accepted but ignored
- Non-JSON upstream error bodies (HTML gateway pages, plain text) no longer raise during error handling in the Anthropic/Gemini/OpenAI-compat providers
- HTTP 402 responses are classified as payment errors, are fallback-eligible, and cool the account for 5 minutes
- Bare model names on the Gemini-native endpoint (`/v1beta/models/gemini-2.5-flash:generateContent`) are auto-prefixed with `gemini/`
- Fusion panel tasks are cancelled and awaited if the client disconnects mid-panel (no orphaned upstream spend)

## [1.2.0] - 2026-07-05

### Added
- **Rate-limit-aware routing** — inventory key rate limits (`rate_limit_rpm`, `rate_limit_rpd`) now feed the fallback handler: accounts at/over their per-minute or per-day request quota are deprioritized (moved to the end of the try-order) during rotation. Daily counts are seeded from the `usage` table on startup and provider reload
- Unified provider catalog `src/janus/catalog.py` — single source of truth for provider metadata; `dashboard/catalog.py` (14 gateway providers) and `inventory/catalog.py` (29 inventory providers) now derive from it, along with the `google`↔`gemini` / `dashscope`↔`qwen` id bridges

### Fixed
- Key Inventory: provider/status/search filters no longer reset after a few seconds while validation is in progress (stale auto-refresh poll could overwrite the filtered table and re-arm itself with the old filter). Poll and user actions are now coordinated with `hx-sync`, and the toolbar buttons + filter form stay in sync with the current view.
- DashScope (Qwen) inventory keys are now picked up for the `qwen` routing prefix (the `qwen`→`dashscope` id bridge was missing, so those keys were silently ignored by routing)

### Changed
- `AGENTS.md` now correctly documents that account cooldowns persist to SQLite (`storage/cooldowns.py`) instead of being in-memory only
- CI now runs `mkdocs build --strict` so docs breakage fails the main pipeline

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
