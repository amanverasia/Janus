# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.1.0] - 2026-08-26

### Added

- **Unified provider control plane** — provider setup, credential inventory, live model discovery,
  custom model registration, visibility controls, and routing state now share one catalog-backed
  workflow inspired by OpenCodeX while retaining Janus's multi-user gateway architecture
- **Model catalog management** — the Cloudline dashboard provides a dedicated Models workspace for
  searching 1,000+ discovered models, enabling or hiding catalog entries, and adding custom models
  without restarting the gateway
- **Provider runtime drivers** — provider-specific authentication, headers, endpoint behavior, model
  normalization, and capability metadata are resolved through reusable drivers and durable routing
  snapshots

### Changed

- **Cloudline is the sole dashboard** — `/dashboard` and former page URLs redirect to
  `/dashboard/ui`; the legacy server-rendered HTMX interface, partial endpoints, templates, scripts,
  stylesheets, and vendored browser dependencies have been removed
- **Inventory-backed routing** — shared upstream credentials and discovered model availability feed
  provider state, routing expansion, health reporting, cooldown behavior, and the public model lists
  without exposing decrypted credentials

### Fixed

- **Provider reload stability** — invalid credentials, partial upstream model responses, duplicate
  provider prefixes, disabled models, and transient catalog failures no longer destabilize the
  active routing snapshot

## [3.0.1] - 2026-08-26

### Fixed

- **Inventory key validation** — automatic checks pause after three consecutive failures instead
  of retrying indefinitely, while Test and Recheck actions resume validation with a fresh attempt
  counter

## [3.0.0] - 2026-08-25

### Added

- **Cloudline dashboard** — a responsive SvelteKit 2, Svelte 5, and TypeScript control plane at
  `/dashboard/ui` covers all 18 operational views with deep links, mobile navigation, light/dark
  themes, command search, live usage, interactive routing visibility, and one-time secret actions
- **Dashboard state API** — authenticated, non-cacheable JSON endpoints under
  `/dashboard/api/v2/state/{section}` provide stable contracts for modular present and future
  control-plane clients
- **Reproducible frontend packaging** — the dashboard build, exact lockfile, local static bundle,
  wheel/sdist contents, Docker image, and CI are checked together without runtime CDN dependencies

### Changed

- **Modular dashboard architecture** — Cloudline is split into reusable components and independent
  feature pages so new routing, inventory, analytics, and future peer-topology modules can be added
  without rebuilding a monolithic server-rendered interface
- **Progressive dashboard migration** — the existing `/dashboard` HTMX interface remains available
  while `/dashboard/ui` becomes the modern application surface on the same FastAPI backend
- **Inventory mutations** — add, import, encryption, and credential-management workflows support
  safe JSON responses for Cloudline while retaining their legacy HTML/HTMX behavior

### Security

- **Credential-safe browser contracts** — state responses recursively strip secrets, explicit reveal
  actions remain non-cacheable and time-limited, untrusted values use safe DOM rendering, and all
  Cloudline routes honor Janus's API-key-only dashboard authentication policy

## [2.2.2] - 2026-08-25

### Fixed

- **API-key copying** — the dashboard now provides a working one-time full-key copy action after
  key creation, labels stored-key actions as prefix copies, and falls back when the modern
  Clipboard API is unavailable or rejected

## [2.2.1] - 2026-08-25

### Security

- **API-key-only dashboard login** — dashboard authentication now applies to localhost and remote
  clients alike; username/password login and the loopback bypass are removed, DB keys require
  `can_login=true`, and legacy dashboard credential settings are purged during initialization

### Fixed

- **Dashboard access editing** — dynamically loaded API-key edit forms are initialized before
  submission, so disabling a key's dashboard access persists instead of navigating with form data
  in the URL

## [2.2.0] - 2026-08-25

### Security

- **Credential-safe inventory views** — ordinary dashboard responses no longer embed decrypted
  upstream credentials; authenticated users must explicitly reveal or copy a credential, revealed
  values are served with no-store headers, and the browser clears them after 30 seconds
- **Dashboard DOM rendering** — toast messages, live activity, and provider verification results
  now use safe text/DOM construction instead of interpreting dynamic values as HTML

### Added

- **Reporting timezone** — Today's Spend, daily budget enforcement, analytics, alerts, and retry
  windows share a configurable IANA timezone with daylight-saving-aware UTC query bounds
- **Self-hosted dashboard assets** — pinned Tailwind, HTMX, Chart.js, D3, and d3-sankey assets ship
  with licenses, checksums, and a reproducible updater, removing the runtime CDN dependency

### Fixed

- **Budget validation** — malformed amounts and invalid key selections return actionable validation
  responses instead of server errors
- **Inventory history quality** — no-op status transitions are no longer recorded or displayed,
  while historical balance snapshots appear as unit-neutral credits

## [2.1.5] - 2026-08-17
### Added
- **Gorouter provider** — `gorouter` (`https://gorouter.app/v1`) is registered in the provider catalog as an OpenAI-compatible gateway with its Claude models (`claude-opus-5-thinking`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-8-thinking`); gorouter keys are validated with a real chat probe
### Fixed
- **Cloudflare-protected upstreams** — outbound requests (key validation/probes, dashboard "fetch models", and runtime OpenAI-compatible calls) now send a browser-like User-Agent by default, so gateways behind Cloudflare bot protection (gorouter / new-api style) no longer return a 403 to plain `httpx` fingerprints

## [2.1.4] - 2026-08-14

### Fixed

- **Codex usage-limit cooldowns** — 429 `usage_limit_reached` bodies (`resets_at` / `resets_in_seconds`) now set a precise per-model cooldown instead of a blind exponential backoff, so exhausted ChatGPT accounts are not hammered on every request
- **Codex in-stream errors** — "Selected model is at capacity" and `server_is_overloaded` events inside 200-OK SSE streams surface as a routable 503 (rotating to the next account) instead of a broken client stream
- **Gemini/Antigravity rate-limit resets** — `google.rpc.RetryInfo` retry delays and "retry in Ns" / "reset after XhYmZs" message phrases in 429 bodies are honored as cooldown durations
- **GitHub Copilot monthly limits** — 402 premium-request exhaustion cools the account down toward the next UTC month instead of retrying every five minutes
- **Kiro account rotation** — bare 400 "Improperly formed request." (CodeWhisperer's quota/account signal), "request not allowed", and "no credentials" errors rotate to the next account instead of failing the request
- **Rate-limit headers** — `x-ratelimit-reset-after` and `x-ratelimit-reset` headers are parsed alongside `Retry-After`

### Added

- **Fallback observability** — the "All providers exhausted" 503 detail and request log now list every attempted account (`account: status`), and each fallback is logged at WARNING level

## [2.1.3] - 2026-08-14

### Fixed

- **Kiro EventStream completeness** — decode AWS `:event-type` headers and surface `toolUseEvent`, `reasoningContentEvent`, `messageStopEvent`, and `metricsEvent` in both stream and JSON paths; strip `<thinking>` blocks while keeping `codeEvent` content raw; map stop reasons to OpenAI finish reasons; remove unavailable `claude-sonnet-4-thinking` from default models
- **Kiro vision** — preserve inline `data:` / base64 image parts when translating canonical requests to Kiro's native envelope
- **Antigravity duplicate finish reason** — `GeminiStreamParser` now emits each stop reason once and still reports late-arriving usage metadata

## [2.1.2] - 2026-08-14

### Fixed

- **Kiro agent compatibility** — native request translation now preserves Pi system prompts, tools, tool results, and multi-turn history; AWS EventStream responses are converted to correctly framed OpenAI SSE/JSON, with auth-aware Kiro/CodeWhisperer/Amazon Q endpoint selection and AWS/social credential refresh
- **Kiro inventory and model discovery** — Kiro credentials can be imported, natively validated, and used for live `ListAvailableModels` discovery; inventory-backed gateways try alternate active accounts when an otherwise valid account cannot list models
- **Antigravity agent compatibility** — Cloud Code response envelopes are unwrapped, imported OAuth credentials refresh reliably, complex Pi tool schemas are reduced to Antigravity's supported Gemini subset, and tool call/result IDs and names survive multi-turn Claude requests

## [2.1.1] - 2026-08-07

### Fixed
- **Custom provider keys now appear in Key Inventory** — keys added via the Providers page are mirrored into `upstream_keys` (the canonical routable store) on create/update/delete, with a one-time backfill on startup for existing/seeded providers. Custom providers with arbitrary prefixes get an `inventory_providers` row so the key is searchable and visible; known-catalog prefixes (openai, anthropic, …) map to their existing inventory id. Mirrored keys go through the normal background validation probe. Closes #71

## [2.1.0] - 2026-08-07

### Added
- **Key Inventory "Test" button** — per-key synchronous test that sends a minimal "hi" chat completion (reusing `validate_key`'s models + usability probe) and shows the result inline (pass/fail + the actual error message), non-persisting. Complements the existing background Recheck action
- **Bulk-select + soft archive on Key Inventory** — checkbox column with select-all-on-page, a bulk-action toolbar (Archive / Restore / Recheck / Delete) that survives HTMX partial swaps, and a "Select all N matching" affordance across filtered pages. Archive is a new reversible `is_archived` state: archived keys are hidden by default, excluded from routing/counts/recheck-all, and restorable under a new `archived` status filter without losing their validation status

## [2.0.0] - 2026-07-28

### Added
- **Cooldown override controls** — Settings/CLI toggle `server_cooldowns_enabled` (default on) to disable account cooldown enforcement and marking; Routing **Clear all cooldowns** wipes active memory + SQLite timers without changing the toggle

## [1.8.0] - 2026-07-28

### Added
- **Codex inventory credentials** — paste Janus OAuth JSON, 9router Codex connection objects, or `providerConnections` arrays into Key Inventory; select Codex (ChatGPT); validate via OAuth refresh; expand into multi-account `codex/<model>` routing while keeping Providers-page paste working

## [1.7.0] - 2026-07-28

### Added
- **Ollama Cloud provider** — first-class catalog entry (`openai_compat`, `https://ollama.com/v1`, prefix `ollama`) with dashboard Fetch Models / Test Connection and inventory-backed multi-key routing
- **Ollama Cloud inventory validation** — keys are validated via an authenticated one-token chat probe (`gpt-oss:20b`) because `/v1/models` is public without a key; routable keys auto-provision the gateway provider

## [1.6.0] - 2026-07-27

### Added
- **Dashboard control room** — Overview redesigned as an at-a-glance status hub: system health strip, attention panel with prioritized actionable alerts, setup checklist for fresh installs, and a live activity widget polling `/dashboard/api/usage/snapshot`
- **Centralized dashboard alerts** — `collect_dashboard_alerts()` aggregates budget warnings, quota exhaustion, routing cooldowns, unhealthy inventory keys, unpriced models, and setup gaps; global banner on all dashboard pages
- **Routing live state** — Routing page highlights the next-available account per prefix, HTMX-refreshes every 8s, ticks cooldown timers, and surfaces sticky routing pins via `FallbackHandler.routing_snapshot()`
- **Analytics trust flags** — banner and breakdown badges when models have token usage but no pricing entry
- **Toast notifications** — replaces browser `alert()` on Providers/Combos HTMX failures; global error handler in the dashboard shell
- **Missing provider logos** — SVG assets for Qwen, OpenCode, GitHub Copilot, and custom providers
- **Live pricing catalog** — Janus now syncs real model pricing from LiteLLM's community price table (~3k models) and OpenRouter's models API into a local `pricing_catalog` table: on startup when stale, on a 24h schedule (`PRICING_SYNC_INTERVAL_HOURS`, `PRICING_SYNC_ENABLED`), and on demand via the Pricing tab's "Sync now" button or `janus pricing sync`. Fail-open: a failed fetch keeps the last-synced catalog. Pricing resolution is layered: user override > synced catalog > builtin, with prefix matching per layer (an override always wins when it matches)
- **Pricing tab upgrades** — sync status line (model count + last-synced age, amber when stale), per-row source badges (override / catalog / builtin), searchable collapsed catalog section, and an "Unpriced models seen recently" table with one-click override prefill
- **`janus pricing backfill [--days N] [--dry-run]`** — retroactively recompute `usage.cost` for $0-cost rows (including cache-only rows) using current pricing; prints how much today's measured spend increased (budgets enforce against it)

### Fixed
- **Subscription/OAuth providers no longer accrue phantom cost** — Copilot/Kiro/Cursor/Codex/Claude-sub/Antigravity/MiMo/OpenCode usage is recorded with $0 cost even when the synced catalog knows the bare model id; backfill and the unpriced-models table exclude subscription rows. Previously copilot/gpt-4o traffic was billed at API list price into budget enforcement
- **Leaderboard nav highlight** — sidebar active state now marks the Leaderboard page correctly

## [1.5.0] - 2026-07-10

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

- **API key scopes** — DB-managed keys support `can_login` (default on; opt out for API-only keys that cannot open the dashboard) and `allowed_models` (exact IDs or `prefix/*` wildcards; empty/unset = all models). Disallowed models return `403` with `error.type = model_not_allowed`; `GET /v1/models` is filtered the same way. Optional daily budget can be set on key create/update via CLI (`--daily-budget`) or the dashboard Keys page. Spec: `docs/superpowers/specs/2026-07-09-api-key-scopes-design.md`
- **Ollama `/api/show` and `/api/generate`** — `POST /api/show` returns stub model metadata for routable models; `POST /api/generate` accepts a bare `prompt` and remaps chat responses to Ollama's completion shape. `GET /api/tags` now respects the API key model allowlist (parity with `GET /v1/models`)
- **Request log retention setting** — `server_request_log_retention` (default 500, clamp 50–5000) replaces the hardcoded row cap; configurable from Settings
- **Request logs pagination** — dashboard request-logs table supports Prev/Next paging via HTMX partial
- **Quota UX round 2** — shared `quota_status` helper drives ≥80% amber banners on provider cards, quota fields on the Routing page, and an 8s poll refresh for provider usage bars
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
- **Request logging coverage** — debug mode now captures pre-routing rejections (budget exceeded, model not allowed), non-fallback upstream errors, and empty-stream failures (not only successes and the final 503)

### Changed
- **Docs** — README feature list and Client Setup cover Phase 8 surfaces (Responses/Codex `config.toml`, Ollama, Gemini CLI, Copilot OAuth, quotas, request logging, Headroom); docs index provider count aligned to 29

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
