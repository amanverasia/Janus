# Janus — TODO & improvements

Living backlog from repo audit (2026-07-05); last updated 2026-07-21 after cooldown/Ponytail fixes and tracker reconciliation. Items are grouped by area; order within a section is rough priority.

---

## High priority

- [x] **Fill `[Unreleased]` in `CHANGELOG.md`** — section is empty; track in-flight work there before each release. *(Done 2026-07-05 — seeded with inventory filter fix + CI/docs changes.)*
- [x] **Update `AGENTS.md`** — still says cooldown state is in-memory only; cooldowns now persist in SQLite (`storage/cooldowns.py`). Fix so agents/docs do not mislead contributors. *(Done 2026-07-05.)*
- [x] **Add `mkdocs build --strict` to CI** — docs workflow only runs on `docs/` changes; main CI (`ci.yml`) should fail if the published site would break. *(Done 2026-07-05.)*
- [x] **Unify provider catalogs** — `dashboard/catalog.py` has 14 gateway providers; `inventory/catalog.py` has 29. Duplicated base URLs, models, and metadata drift easily. Single source or codegen sync. *(Done 2026-07-05 — unified into `src/janus/catalog.py`; both legacy modules and the id bridges now derive from it. Also fixed the missing `qwen`→`dashscope` prefix bridge so DashScope inventory keys route for the `qwen` prefix.)*
- [x] **Use inventory rate limits in routing** — `rate_limit_rpm/tpm/rpd` are stored and shown in the UI but ignored by `routing/fallback.py` and `list_routable_upstream_keys()`. Skip or deprioritize keys near quota exhaustion during rotation. *(Done 2026-07-05 for RPM/RPD — accounts without headroom are moved to the end of the try-order; TPM enforcement deferred since token counts are only known post-response.)*
- [x] **API key scopes** — per-key `can_login` (API-only vs dashboard) and `allowed_models` (exact IDs + `prefix/*`; empty = all), plus optional daily budget on key create/update. Spec: `docs/superpowers/specs/2026-07-09-api-key-scopes-design.md`. *(Done 2026-07-09 — storage columns + migration, `key_access` matching, auth/dashboard/API enforcement, CLI `keys update`, dashboard Keys UI.)*

---

## Phase 8 — 9router feature parity (plan: `docs/superpowers/specs/2026-07-05-phase8-9router-parity.md`)

Gap review vs [9router](https://github.com/decolua/9router) done 2026-07-05. Order = implementation order.

- [x] **8.1 OpenAI Responses API format adapter** — `formats/openai_responses.py` (`/v1/responses` inbound). Codex CLI speaks this natively. Follows the six-method adapter recipe; register in `FORMATS`. *(Done 2026-07-05 — bidirectional adapter incl. streaming parser/emitter for future Codex upstream use; unit + integration tests.)*
- [x] **8.2 Request logging / debug mode** — opt-in capture of full request/response (headers redacted, bodies truncated) to SQLite; dashboard viewer page + export; toggle in Settings. Off by default. *(Done 2026-07-05 — `storage/request_logs.py`, `/dashboard/request-logs`, `server_request_logging` setting; 64 KB truncation, 500-row retention, fire-and-forget writes.)*
- [x] **8.3 Headroom token saver** — optional external `/v1/compress` proxy hop in `SaverPipeline` (fail-open, like all savers). Config: base URL + enable toggle. *(Done 2026-07-05 — `tokensavers/headroom.py`, `AsyncTokenSaver` protocol + `apply_async` pipeline stage, dashboard card with URL field.)*
- [x] **8.4 OAuth provider framework** — token store (encrypted at rest), refresh-before-expiry loop, `OAuthProvider` executor base. First provider: GitHub Copilot (device-code flow, best documented). Follow-ons: Kiro, Codex/ChatGPT, Claude Code subscription. *(Done 2026-07-05 for Copilot — `providers/github_copilot.py` with device-flow helpers + session-token refresh (single-flight); long-lived GitHub token lives in `providers.api_key` (no separate oauth_tokens table needed — GitHub device-flow tokens have no refresh token/expiry); dashboard Connect flow + fetch-models/test branches. Follow-on OAuth providers (Kiro, Codex, Claude Code) still open, tracked below.)*
- [ ] **8.4b Complete additional OAuth onboarding** — Codex, Kiro, and Claude OAuth executors can refresh manually supplied credential blobs, but still need user-facing auth/device flows; Cursor remains API-key based, and Vertex service-account JSON onboarding is also open. Reuse the Copilot dashboard Connect pattern. Consider encrypting `providers.api_key` at rest (reuse `inventory/key_encryption.py`).
- [x] **8.5 Subscription quota tracking** — per-provider quota windows (5h / daily / weekly / monthly) with reset countdowns in dashboard; feed window exhaustion into fallback ordering. Depends on 8.4. *(Done 2026-07-05 — `storage/quotas.py` UTC windows, `quota_window/limit/metric` provider columns, FallbackHandler in-memory counters seeded from usage table, soft deprioritization, provider-card usage bar + countdown.)*
- [x] **8.6 Ollama inbound format adapter** — `/api/chat` + `/api/tags` shims for tools that only support Ollama endpoints. *(Done 2026-07-05 — `formats/ollama.py` with NDJSON streaming (`stream_media_type` adapter attr), positional tool-call id assignment, `/api/version` handshake endpoint. Phase 8 parity plan complete except 8.4b follow-on OAuth providers.)*

Explicitly out of scope (anti-goals): cloud sync (conflicts with local-first design; YAML export + DB copy is the answer), Cloudflare Workers deploy (Node-only), i18n READMEs.

### Phase 8 follow-ups (discovered during implementation)

- [x] **Request logs: capture non-fallback upstream errors** — 8.2 logs successes, streams, and the final 503; upstream 4xx errors that raise `HTTPException` directly (non-fallback-eligible) bypass logging. Wrap those raise paths so debug mode sees failed requests too. *(Done 2026-07-09 — pre-routing errors (budget, allowlist) and empty-stream failures now logged via `_log_error_and_raise`.)*
- [x] **Request logs: configurable retention + pagination** — 500-row cap and 64 KB truncation are hardcoded (`storage/request_logs.py`); the dashboard shows only the latest 100 with no paging. Expose retention in Settings and paginate the table (reuse the keys-table pagination pattern). *(Done 2026-07-09 — `server_request_log_retention` setting (50–5000, default 500); HTMX Prev/Next pagination on request-logs page.)*
- [x] **Quota UX round 2** — near-exhaustion warning (dashboard banner at ≥80%, mirroring budget warn), quota state on the Routing page next to cooldowns/rate limits, and live refresh of the provider-card usage bar (currently render-time only). *(Done 2026-07-09 — shared `quota_status` helper, amber banners, routing overview quota fields, 8s providers partial poll.)*
- [ ] **True rolling 5h quota windows** — `storage/quotas.py` uses fixed UTC 5-hour buckets as an approximation; Claude/Codex subscriptions use rolling windows anchored to first request. Needs per-window timestamp tracking (deque or window-anchor column).
- [ ] **Copilot reference pricing / savings view** — Copilot is deliberately treated as subscription-covered, so marginal usage cost records as $0 even when equivalent API pricing exists. Add a separate "what this would cost via API" estimate or explicitly label the traffic as subscription-covered rather than changing billed cost.
- [x] **Ollama surface completeness** — some Ollama clients call `POST /api/show` (model metadata) and `POST /api/generate` (bare completions) before/instead of `/api/chat`. Add minimal shims so those clients don't fail the handshake. `/api/version` is already stubbed. *(Done 2026-07-09 — `/api/show` metadata shim, `/api/generate` prompt→chat remap, `/api/tags` filtered by key allowlist.)*
- [ ] **Copilot session-token 401 mid-stream** — a session token revoked between refresh and stream start surfaces as a stream error (no retry, by design). Consider a one-shot forced token refresh + retry for the non-streaming path.

---

## Routing & gateway

- [ ] **Smarter inventory account ordering** — today: `priority DESC`, then credits. Consider health status, recent 429s, and RPM headroom in sort/rotation.
- [ ] **Streaming fallback story** — mid-stream errors cannot retry (by design). Document clearly for users; optionally explore safe reconnect patterns for idempotent short streams.
- [ ] **Gateway-level rate limiting** — inventory submit is rate-limited; public `/v1/*` API is not. Add optional per-key RPM limits on the Janus API itself.
- [ ] **Richer `/v1/health`** — today returns `{"status":"ok"}`. Add optional checks: DB reachable, provider count, inventory scheduler alive, last recheck age.
- [x] **OAuth / subscription providers** — deferred since Phase 1 (Codex, ChatGPT Plus, etc.). Needs token refresh, secure storage, and provider executors beyond API-key types. *(Done 2026-07-05 for the framework + GitHub Copilot — see Phase 8.4. Remaining providers tracked as 8.4b.)*

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
- [ ] **Monthly / rolling budgets** — budgets are daily only (`storage/budgets.py`). *(Note: per-provider subscription quotas (Phase 8.5) now cover weekly/monthly consumption windows, but spend budgets — dollar limits per key/global — remain daily-only.)*

---

## Dashboard & UX

- [ ] **Replace `alert()` error handling** — `providers.html` and `combos.html` still use browser alerts on HTMX failures; use inline toasts like other pages.
- [ ] **Vendor CDN dependencies** — Tailwind, HTMX, Chart.js, D3 load from CDNs in `base.html` / `analytics.html`. Bundle for offline, air-gapped, and CSP-hardened deployments.
- [ ] **Missing provider logos** — several dashboard catalog entries have empty `logo` (Qwen, OpenCode, custom, GitHub Copilot). Add SVGs under `dashboard/static/logos/`.
- [x] **Align README marketing numbers** — README says "40+ providers" and inventory "29 providers"; dashboard catalog is 14. Pick consistent, accurate counts. *(Done 2026-07-05 — README now says "29 built-in providers, or any OpenAI-compatible endpoint".)*
- [ ] **Routing page enhancements** — show live rotation index, sticky-routing state per client key, and estimated try-order after cooldowns expire.

---

## Docs & developer experience

- [ ] **Document maintainer scripts in user docs** — currently only in `CONTRIBUTING.md`; add a short "Maintenance" subsection to deployment or contributing on the docs site (already included via snippet — verify it renders on GitHub Pages).
- [x] **Refresh README feature list** — README predates Phase 8; add Responses API (Codex-native), Ollama endpoints, GitHub Copilot OAuth, subscription quotas, request logging, and Headroom to the feature summary and any endpoint tables. *(Done 2026-07-10.)*
- [x] **Client setup for new surfaces** — extend `docs/client-setup.md` with Codex CLI via `/v1/responses` and Ollama-only tools via `/api/chat` (including the `stream` default and NDJSON note). *(Done 2026-07-10 — Codex `config.toml` + Responses; Ollama show/generate/tags; Gemini CLI section expanded.)*
- [ ] **Trim or relocate `docs/superpowers/`** — 28 historical plans/specs from June–July 2026; excluded from the site and now labeled as archival, but still add repo noise. Archive to a wiki, separate branch, or compress into `docs/architecture.md` history.
- [ ] **TLS / reverse-proxy guide expansion** — Janus is HTTP-only; add Caddy/nginx/Tailscale Serve examples with auth headers and WebSocket/SSE notes for streaming clients.
- [x] **Client setup for Gemini-native tools** — inbound Gemini endpoint exists; ensure `client-setup.md` covers Cursor/Gemini CLI paths completely. *(Done 2026-07-10 — Gemini CLI env vars + Cursor vs Gemini surface note.)*

---

## CI, testing & quality

- [ ] **Tests for maintainer scripts** — `generate_model_catalog.py` and `seed_openrouter_pricing.py` have no unit tests (mock httpx / sample TS fixture).
- [ ] **Python 3.12 in CI matrix** — `pyproject.toml` classifiers include 3.12; CI only runs 3.11.
- [ ] **Coverage reporting** — `pytest-cov` is a dev dep but unused in CI; add threshold or upload to catch untested routing/inventory paths.
- [ ] **Lint `scripts/` in CI** — `ruff check` targets `src/janus/` and `tests/` only; scripts can drift.

---

## Architecture & tech debt

- [ ] **Move `_build_provider()` out of `app.py`** — called from lifespan and reload; belongs in `providers/` factory module for clearer boundaries.
- [x] **Encrypt `providers.api_key` at rest** — gateway API keys and OAuth credential blobs now reuse `INVENTORY_ENCRYPTION_KEY`, encrypt on storage writes/YAML seed, decrypt at the storage boundary, and migrate with the unified `janus inventory encrypt-keys` CLI/dashboard action. *(Done 2026-07-21 — mixed plaintext compatibility, explicit missing/wrong-key failures, and CRUD/seed/migration/UI coverage.)*
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
- Webhook integrations (budget warn, key exhausted, quota near-exhaustion, provider down).
- Provider auto-discovery from a single master API key (OpenRouter-style routing without manual prefix setup).
- Mid-stream resume / partial output caching for long agent runs.
- Savings tracker view — with subscription quotas + pricing data, show "what this month would have cost via paid APIs" (9router's cost-display framing) as a dashboard stat.
- Anthropic-format upstream for Copilot/Claude-family OAuth providers once 8.4b lands (today all OAuth routing assumes OpenAI-compatible upstreams).

## Phase 3 follow-ups (2026-07-10, from final review of feat/9router-highclass)

- [x] **Stale-cooldown Retry-After understatement** — `earliest_cooldown_expiry` (routing/fallback.py) counted already-expired entries, so a stale expired `__all__` row could shrink Retry-After to 1s while a 300s model cooldown was active. *(Done 2026-07-21 — future-expiry filtering plus direct and full `resolve_attempts()` regressions.)*
- [ ] **Streaming pre-body error classification** — the streaming error site that runs before any bytes are sent could safely use the refined body-text classification (retry is still possible there); today only non-stream sites use it.
- [ ] **Fusion request log records judge model, not combo name** — Monitor tab shows e.g. `a/m1` for a request that asked for combo `fus`; consider logging the combo name (or both).
- [ ] **Body-text markers only inspect `error` key for dict bodies** — providers returning `{"message": "rate limit"}` at top level are missed by refine_error_type.
- [ ] **Judge-probe rotation drift** — `_pick_available_judge` probes resolve_attempts, advancing rotation counters once before the real attempt loop; cosmetic fairness drift.
- [x] **Ponytail reload lacks fail-open level guard** — invalid `saver_ponytail_level` in DB crashed reload. *(Done 2026-07-21 — invalid persisted levels now fall back to `full`, matching Caveman, with reload-level integration coverage.)*
- [ ] **Sticky/rotation counter persistence across restarts** — 9router persists consecutiveUseCount/lastUsedAt per connection; Janus is in-memory (survives reload via adopt_runtime_state, resets on restart). Low value.
- [ ] **Rolling (vs fixed UTC) quota windows** — Claude/Codex subscriptions use rolling windows anchored to first request; needs per-window anchor timestamps.
