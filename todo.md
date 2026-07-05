# Janus — TODO & improvements

Living backlog from repo audit (2026-07-05). Items are grouped by area; order within a section is rough priority.

---

## High priority

- [x] **Fill `[Unreleased]` in `CHANGELOG.md`** — section is empty; track in-flight work there before each release. *(Done 2026-07-05 — seeded with inventory filter fix + CI/docs changes.)*
- [x] **Update `AGENTS.md`** — still says cooldown state is in-memory only; cooldowns now persist in SQLite (`storage/cooldowns.py`). Fix so agents/docs do not mislead contributors. *(Done 2026-07-05.)*
- [x] **Add `mkdocs build --strict` to CI** — docs workflow only runs on `docs/` changes; main CI (`ci.yml`) should fail if the published site would break. *(Done 2026-07-05.)*
- [x] **Unify provider catalogs** — `dashboard/catalog.py` has 14 gateway providers; `inventory/catalog.py` has 29. Duplicated base URLs, models, and metadata drift easily. Single source or codegen sync. *(Done 2026-07-05 — unified into `src/janus/catalog.py`; both legacy modules and the id bridges now derive from it. Also fixed the missing `qwen`→`dashscope` prefix bridge so DashScope inventory keys route for the `qwen` prefix.)*
- [x] **Use inventory rate limits in routing** — `rate_limit_rpm/tpm/rpd` are stored and shown in the UI but ignored by `routing/fallback.py` and `list_routable_upstream_keys()`. Skip or deprioritize keys near quota exhaustion during rotation. *(Done 2026-07-05 for RPM/RPD — accounts without headroom are moved to the end of the try-order; TPM enforcement deferred since token counts are only known post-response.)*

---

## Phase 8 — 9router feature parity (plan: `docs/superpowers/specs/2026-07-05-phase8-9router-parity.md`)

Gap review vs [9router](https://github.com/decolua/9router) done 2026-07-05. Order = implementation order.

- [x] **8.1 OpenAI Responses API format adapter** — `formats/openai_responses.py` (`/v1/responses` inbound). Codex CLI speaks this natively. Follows the six-method adapter recipe; register in `FORMATS`. *(Done 2026-07-05 — bidirectional adapter incl. streaming parser/emitter for future Codex upstream use; unit + integration tests.)*
- [x] **8.2 Request logging / debug mode** — opt-in capture of full request/response (headers redacted, bodies truncated) to SQLite; dashboard viewer page + export; toggle in Settings. Off by default. *(Done 2026-07-05 — `storage/request_logs.py`, `/dashboard/request-logs`, `server_request_logging` setting; 64 KB truncation, 500-row retention, fire-and-forget writes.)*
- [x] **8.3 Headroom token saver** — optional external `/v1/compress` proxy hop in `SaverPipeline` (fail-open, like all savers). Config: base URL + enable toggle. *(Done 2026-07-05 — `tokensavers/headroom.py`, `AsyncTokenSaver` protocol + `apply_async` pipeline stage, dashboard card with URL field.)*
- [x] **8.4 OAuth provider framework** — token store (encrypted at rest), refresh-before-expiry loop, `OAuthProvider` executor base. First provider: GitHub Copilot (device-code flow, best documented). Follow-ons: Kiro, Codex/ChatGPT, Claude Code subscription. *(Done 2026-07-05 for Copilot — `providers/github_copilot.py` with device-flow helpers + session-token refresh (single-flight); long-lived GitHub token lives in `providers.api_key` (no separate oauth_tokens table needed — GitHub device-flow tokens have no refresh token/expiry); dashboard Connect flow + fetch-models/test branches. Follow-on OAuth providers (Kiro, Codex, Claude Code) still open, tracked below.)*
- [ ] **8.4b Additional OAuth providers** — Kiro (free Claude), Codex/ChatGPT subscription, Claude Code subscription, Vertex service-account JSON. Each needs its own auth flow + executor; reuse the Copilot pattern (token exchange in executor, connect flow in dashboard). Consider encrypting `providers.api_key` at rest (reuse `inventory/key_encryption.py`).
- [x] **8.5 Subscription quota tracking** — per-provider quota windows (5h / daily / weekly / monthly) with reset countdowns in dashboard; feed window exhaustion into fallback ordering. Depends on 8.4. *(Done 2026-07-05 — `storage/quotas.py` UTC windows, `quota_window/limit/metric` provider columns, FallbackHandler in-memory counters seeded from usage table, soft deprioritization, provider-card usage bar + countdown.)*
- [x] **8.6 Ollama inbound format adapter** — `/api/chat` + `/api/tags` shims for tools that only support Ollama endpoints. *(Done 2026-07-05 — `formats/ollama.py` with NDJSON streaming (`stream_media_type` adapter attr), positional tool-call id assignment, `/api/version` handshake endpoint. Phase 8 parity plan complete except 8.4b follow-on OAuth providers.)*

Explicitly out of scope (anti-goals): cloud sync (conflicts with local-first design; YAML export + DB copy is the answer), Cloudflare Workers deploy (Node-only), i18n READMEs.

---

## Routing & gateway

- [ ] **Smarter inventory account ordering** — today: `priority DESC`, then credits. Consider health status, recent 429s, and RPM headroom in sort/rotation.
- [ ] **Streaming fallback story** — mid-stream errors cannot retry (by design). Document clearly for users; optionally explore safe reconnect patterns for idempotent short streams.
- [ ] **Gateway-level rate limiting** — inventory submit is rate-limited; public `/v1/*` API is not. Add optional per-key RPM limits on the Janus API itself.
- [ ] **Richer `/v1/health`** — today returns `{"status":"ok"}`. Add optional checks: DB reachable, provider count, inventory scheduler alive, last recheck age.
- [ ] **OAuth / subscription providers** — deferred since Phase 1 (Codex, ChatGPT Plus, etc.). Needs token refresh, secure storage, and provider executors beyond API-key types. *(Tracked as Phase 8.4 above.)*

---

## Inventory

- [ ] **Decouple model catalog from `Dashboard_For_Apis`** — `generate_model_catalog.py` defaults to a sibling-repo TypeScript file. Maintain catalog JSON in-repo (or fetch from a published artifact) so contributors are not blocked.
- [ ] **Inventory scheduler in dashboard** — recheck interval is env-only (`INVENTORY_CHECK_INTERVAL_HOURS`, default 12h). README says "twice daily"; expose interval + enable/disable in Settings.
- [ ] **Low-credit / exhausted-key alerts** — health warnings exist in DB/UI; no proactive notification (dashboard banner, webhook, or email) when keys hit critical/exhausted.
- [ ] **Bulk export for inventory keys** — import from Dashboard_For_Apis JSON exists; symmetric export (encrypted/masked) for backup and migration is missing.
- [ ] **Expand inventory tests for push API edge cases** — pagination, encryption round-trip, and concurrent ingest under rate limit.

---

## Pricing, budgets & analytics

- [ ] **Expand builtin pricing** — ~28 models in `pricing/builtin.py`; many inventory/routed models still cost **$0** when unknown. Run `seed_openrouter_pricing.py` periodically or widen defaults.
- [ ] **Surface $0-cost warnings in analytics** — when `compute_cost` returns 0 for an unknown model, flag it in Usage/Analytics so spend looks wrong.
- [ ] **Automate pricing refresh** — document + optional CLI/cron for OpenRouter seed; consider similar for other catalogs.
- [ ] **Budget notifications beyond HTTP 429** — warn at 80% is silent to the user until the next blocked request. Dashboard banner or webhook at warn threshold.
- [ ] **Monthly / rolling budgets** — budgets are daily only (`storage/budgets.py`).

---

## Dashboard & UX

- [ ] **Replace `alert()` error handling** — `providers.html` and `combos.html` still use browser alerts on HTMX failures; use inline toasts like other pages.
- [ ] **Vendor CDN dependencies** — Tailwind, HTMX, Chart.js, D3 load from CDNs in `base.html` / `analytics.html`. Bundle for offline, air-gapped, and CSP-hardened deployments.
- [ ] **Missing provider logos** — several dashboard catalog entries have empty `logo` (Qwen, OpenCode, custom). Add SVGs under `dashboard/static/logos/`.
- [x] **Align README marketing numbers** — README says "40+ providers" and inventory "29 providers"; dashboard catalog is 14. Pick consistent, accurate counts. *(Done 2026-07-05 — README now says "29 built-in providers, or any OpenAI-compatible endpoint".)*
- [ ] **Routing page enhancements** — show live rotation index, sticky-routing state per client key, and estimated try-order after cooldowns expire.

---

## Docs & developer experience

- [ ] **Document maintainer scripts in user docs** — currently only in `CONTRIBUTING.md`; add a short "Maintenance" subsection to deployment or contributing on the docs site (already included via snippet — verify it renders on GitHub Pages).
- [ ] **Trim or relocate `docs/superpowers/`** — 18 phase plans/specs from June 2026; excluded from the site but add repo noise. Archive to a wiki, separate branch, or compress into `docs/architecture.md` history.
- [ ] **TLS / reverse-proxy guide expansion** — Janus is HTTP-only; add Caddy/nginx/Tailscale Serve examples with auth headers and WebSocket/SSE notes for streaming clients.
- [ ] **Client setup for Gemini-native tools** — inbound Gemini endpoint exists; ensure `client-setup.md` covers Cursor/Gemini CLI paths completely.

---

## CI, testing & quality

- [ ] **Tests for maintainer scripts** — `generate_model_catalog.py` and `seed_openrouter_pricing.py` have no unit tests (mock httpx / sample TS fixture).
- [ ] **Python 3.12 in CI matrix** — `pyproject.toml` classifiers include 3.12; CI only runs 3.11.
- [ ] **Coverage reporting** — `pytest-cov` is a dev dep but unused in CI; add threshold or upload to catch untested routing/inventory paths.
- [ ] **Lint `scripts/` in CI** — `ruff check` targets `src/janus/` and `tests/` only; scripts can drift.

---

## Architecture & tech debt

- [ ] **Move `_build_provider()` out of `app.py`** — called from lifespan and reload; belongs in `providers/` factory module for clearer boundaries.
- [x] **Reduce catalog duplication between `dashboard/catalog.py` and `inventory/catalog.py`** — different shapes (`CATALOG` dict vs list with detection endpoints); unify schema. *(Done 2026-07-05 — see "Unify provider catalogs" above.)*
- [ ] **Inventory module packaging** — added `inventory/__init__.py`; consider same explicit exports pattern for other leaf packages if mypy/import clarity suffers.
- [ ] **Structured logging** — request ID, attempt index, and fallback chain logged but not consistently structured for log aggregation.

---

## Release & ops

- [ ] **Release checklist** — bump `pyproject.toml` version, fill `CHANGELOG.md`, tag `v*`, verify PyPI + GHCR publish workflows.
- [ ] **Docker `:latest` vs semver tags** — document which tag to pin in production; consider digest pinning in `docker-compose.yml` comments.
- [ ] **Backup / restore docs** — `./janus-data/` and `~/.janus/janus.db` are the whole state; document export (dashboard YAML export + DB copy) and restore procedure.

---

## Ideas / later (not committed)

- Multi-user RBAC (single-user by design today).
- Built-in HTTPS termination.
- Webhook integrations (budget warn, key exhausted, provider down).
- Provider auto-discovery from a single master API key (OpenRouter-style routing without manual prefix setup).
- Mid-stream resume / partial output caching for long agent runs.
