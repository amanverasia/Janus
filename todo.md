# Janus — TODO & improvements

Living backlog from repo audit (2026-07-05). Items are grouped by area; order within a section is rough priority.

---

## High priority

- [ ] **Fill `[Unreleased]` in `CHANGELOG.md`** — section is empty; track in-flight work there before each release.
- [ ] **Update `AGENTS.md`** — still says cooldown state is in-memory only; cooldowns now persist in SQLite (`storage/cooldowns.py`). Fix so agents/docs do not mislead contributors.
- [ ] **Add `mkdocs build --strict` to CI** — docs workflow only runs on `docs/` changes; main CI (`ci.yml`) should fail if the published site would break.
- [ ] **Unify provider catalogs** — `dashboard/catalog.py` has 14 gateway providers; `inventory/catalog.py` has 29. Duplicated base URLs, models, and metadata drift easily. Single source or codegen sync.
- [ ] **Use inventory rate limits in routing** — `rate_limit_rpm/tpm/rpd` are stored and shown in the UI but ignored by `routing/fallback.py` and `list_routable_upstream_keys()`. Skip or deprioritize keys near quota exhaustion during rotation.

---

## Routing & gateway

- [ ] **Smarter inventory account ordering** — today: `priority DESC`, then credits. Consider health status, recent 429s, and RPM headroom in sort/rotation.
- [ ] **Streaming fallback story** — mid-stream errors cannot retry (by design). Document clearly for users; optionally explore safe reconnect patterns for idempotent short streams.
- [ ] **Gateway-level rate limiting** — inventory submit is rate-limited; public `/v1/*` API is not. Add optional per-key RPM limits on the Janus API itself.
- [ ] **Richer `/v1/health`** — today returns `{"status":"ok"}`. Add optional checks: DB reachable, provider count, inventory scheduler alive, last recheck age.
- [ ] **OAuth / subscription providers** — deferred since Phase 1 (Codex, ChatGPT Plus, etc.). Needs token refresh, secure storage, and provider executors beyond API-key types.

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
- [ ] **Align README marketing numbers** — README says "40+ providers" and inventory "29 providers"; dashboard catalog is 14. Pick consistent, accurate counts.
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
- [ ] **Reduce catalog duplication between `dashboard/catalog.py` and `inventory/catalog.py`** — different shapes (`CATALOG` dict vs list with detection endpoints); unify schema.
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
