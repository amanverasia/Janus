# Changelog

All notable changes to Janus are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Gemini inbound client endpoint — Gemini-native tools can talk to Janus via
  `POST /v1beta/models/{prefix/model}:generateContent` (and `:streamGenerateContent`)
- Dashboard Test-Connection button — probes an upstream provider with a 1-token
  request and reports status + latency
- Dashboard Export Config — download current DB state as YAML
- Dashboard Reset-to-Defaults — clear DB tables and re-seed from `config.yaml`
- Cooldown state persists across server restarts via SQLite
- API key auth accepts Gemini-style `x-goog-api-key` header and `?key=` query param
- Docker image at `ghcr.io/amanverasia/janus` (multi-arch: amd64 + arm64)
- Upstream Key Inventory — store, validate, and route with many API keys across
  27+ providers (dashboard UI, CLI, push API, routing integration)

### Changed

- Dashboard mutations no longer wipe in-memory fallback cooldowns
- Config YAML is a one-time seed — SQLite DB is the runtime source of truth

## [0.2.2] - 2026-06-25

### Fixed

- Provider logos visible on dark background
- API key preserved when editing a provider (leave blank to keep current key)

## [0.2.1] - 2026-06-25

### Added

- Fetch Models button in provider Add/Edit modals
- Official provider logos via Simple Icons CDN
- Root URL `/` redirects to `/dashboard`

## [0.2.0] - 2026-06-25

### Added

- Full dashboard CRUD: providers, combos, token savers, pricing, settings
- Provider catalog with 14 known providers
- DB-driven config with hot-reload (no restart needed)
- Combo editor with drag-and-drop reordering
- Token savers toggle page with Ponytail level selector
- Tool Setup page with copy-paste env vars for Claude Code, Codex, Cursor, Cline
- Pricing page for builtin prices + custom overrides
- Settings page: toggle `require_api_key`, export config, reset to defaults
- Grouped sidebar navigation with 13 pages
- GPLv3 license, PyPI packaging, MkDocs docs site

## [0.1.0] - 2026-06-25

### Added

- Core routing gateway with canonical intermediate model
- Format adapters: OpenAI, Anthropic, Gemini
- Provider executors: openai_compat, anthropic, gemini, opencode_free
- SSE streaming translation
- Multi-account fallback with cooldowns
- Named combos, token savers (RTK, Caveman, Ponytail)
- SQLite persistence, pricing engine, budget enforcement, analytics
- HTMX dashboard, CLI, Docker support, GitHub Actions CI
