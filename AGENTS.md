# AGENTS.md

## Dev environment

- Python 3.11 in a `.venv` at repo root. Always use `.venv/bin/python -m pytest`, not bare `pytest`.
- Install: `pip install -e ".[dev]"` (editable + dev extras: respx, ruff, mypy, mkdocs-material, build).
- CI runs on push/PR via `.github/workflows/ci.yml` (ruff check + format + mypy + pytest).
- PyPI package name is `janus-ai`. Import name is `janus`. CLI binary is `janus`.

## Commands

```bash
.venv/bin/python -m pytest                                    # all tests
.venv/bin/python -m pytest tests/unit/formats/test_openai.py::test_name -v  # single test
.venv/bin/ruff check src/janus/ tests/                        # lint
.venv/bin/ruff format --check src/janus/ tests/              # format check (run before committing)
.venv/bin/mypy src/janus/                                    # typecheck (strict)
.venv/bin/janus serve --port 20128 --config ~/.janus/config.yaml  # dev server
docker compose up -d                                         # Docker (persists in ./janus-data/)
.venv/bin/mkdocs serve                                       # docs preview
.venv/bin/mkdocs build --strict                              # docs verify
.venv/bin/python -m build                                    # wheel + sdist
```

## Architecture constraint

**Canonical intermediate model.** `formats/` and `providers/` never import or call each other — they only talk to `canonical/`. This is intentional (2N adapters instead of N² translators). Do not break this boundary.

Request flow: client format → `parse_request` → `CanonicalRequest` → `SaverPipeline.apply` → budget check (`_check_budgets`) → `FallbackHandler.resolve_attempts` → per-attempt: `build_upstream_request` → upstream call → `parse_upstream_response` → `CanonicalResponse` → `emit_response` → `record_usage` (with cost). On 429/5xx/auth/network errors, the account is cooled down and the next attempt is tried.

## Routing & fallback layer

- `ProviderRegistry` stores `list[ProviderConfig]` per prefix (multi-account). `lookup()` returns `list[ResolvedTarget]`, not a single target.
- `FallbackHandler` (`routing/fallback.py`) expands combos → models → available accounts, filtering out cooled-down accounts. Cooldown durations: 429→60s, 5xx→30s, auth→300s, network→15s. State is in-memory (`time.monotonic()`).
- `routing/errors.py` has `classify_error(status_code)` and `is_fallback_eligible(error)` — these drive fallback decisions in `_handle()`.
- The retry loop lives in `api/routes.py::_handle()`. Streaming requests do NOT retry mid-stream (can't replay partial output).
- Adding multi-account: register multiple `ProviderConfig` entries with the same `prefix` but different `id`/`api_key`.

## Provider lifecycle & connection pooling

- `create_app()` creates an empty `app.state.providers = {}`. Providers are built during `lifespan()` startup via `reload_providers()` (in `dashboard/reload.py`), which reads enabled providers from the `providers` DB table and calls `_build_provider()` (still defined in `app.py`).
- Each provider holds a shared `httpx.AsyncClient` with pool limits (100 connections, 20 keepalive). Clients are NOT created per-request.
- Providers are closed on shutdown via the FastAPI lifespan handler in `app.py`.
- After dashboard CRUD operations, `reload_providers(app)` rebuilds providers, registry, and fallback handler without restart. Deleted/disabled providers have their `httpx.AsyncClient` closed.
- `_handle()` looks up cached providers by `target.provider_config.id` — never constructs providers inline.

## DB-driven config (source of truth)

Providers, combos, token savers, pricing overrides, and server settings live in SQLite, not YAML. The YAML config file (`~/.janus/config.yaml`) is a **seed** — loaded once on first startup via `seed_from_config()` (in `database.py`), which imports into `providers`, `combos`, `settings`, `pricing_overrides` tables. After seeding, the **DB is the source of truth** — editing YAML and restarting will NOT re-seed (idempotent: existing rows are skipped).

Hot-reload helpers in `dashboard/reload.py` rebuild in-memory state from DB after changes: `reload_providers`, `reload_combos`, `reload_savers`, `reload_pricing`. Dashboard mutation routes call these after writing to the DB.

`dashboard/catalog.py` holds the provider catalog (14 known providers with pre-filled `api_type`, `base_url`, default models) that the "Add Provider" UI draws from.

## Adding a new format adapter

1. Create `src/janus/formats/<name>.py` implementing all six methods: `parse_request`, `build_upstream_request`, `parse_upstream_response`, `emit_response`, `stream_parser`, `stream_emitter`.
2. Register in the `FORMATS` dict in `src/janus/api/routes.py`.

## Adding a new provider executor

1. Create `src/janus/providers/<name>.py` with a `call(payload, stream) -> RawResult` method and a `close()` method (the `Provider` protocol requires it).
2. Add a case to `_build_provider()` in **`src/janus/app.py`**.
3. If the provider's native format differs from its `api_type`, update `_resolve_format()` in routes.py.

## Adding a new token saver

1. Implement `TokenSaver` protocol (`transform(req) -> CanonicalRequest`) in `src/janus/tokensavers/`.
2. Add construction logic to `reload_savers()` in **`src/janus/dashboard/reload.py`** (not `app.py`).
3. Savers must be fail-safe — exceptions are caught by the pipeline and logged, never breaking the request.

## Code style (enforced by tooling)

- `ruff` with line-length 100, rules: E, F, I, N, W, UP.
- `mypy --strict` — bare `dict`/`list` must be typed (`dict[str, Any]`). Use `X | Y` not `Union`. Use `StrEnum` not `str, Enum`.
- No code comments unless explicitly requested.
- src layout: package code lives under `src/janus/`, not repo root.

## Testing

- `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions work without `@pytest.mark.asyncio`.
- Provider tests mock httpx with `respx` (no real network calls).
- Integration tests use FastAPI ASGI transport (`httpx.ASGITransport`) in-process.
- Test fixtures (sample API payloads, usage seed helpers) live in `tests/fixtures/`.
- **Dashboard routes do lazy `init_db` guard** because ASGITransport doesn't run the FastAPI lifespan handler. `_ensure_db()` (in `dashboard/routes.py`) now also triggers `seed_from_config()` + all reload functions, so tests that hit dashboard routes get a fully seeded + warmed app. If you add dashboard routes that touch the DB, call `_ensure_db(request)` first.
- API route tests that need providers/combos must call `init_db()` + `seed_from_config()` + reload functions explicitly in their fixture (ASGITransport skips lifespan). See pattern in `tests/integration/test_api.py`.

## SQLite storage

Runtime state in SQLite (`~/.janus/janus.db`). DB is auto-created on app startup via FastAPI lifespan (`app.py`). Schema migrations are idempotent — `init_db()` uses `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` for new columns.

- `storage/api_keys.py` — keys are `sk-janus-{32hex}`, stored as SHA256 hash. `verify_key()` returns `int | None` (DB row ID). The API-key gate (`api/deps.py`) checks both config `api_keys` (static list) AND DB keys. When a DB key is used, `request.state.client_key_id` is set.
- `storage/usage.py` — `record_usage()` records per-request token usage (fire-and-forget, non-streaming only). Params include `cost`, `cache_creation_tokens`, `cache_read_tokens`, `client_key_id`.
- `storage/settings.py` — key-value settings store (`get_setting`, `set_setting`, `get_all_settings`).
- `storage/providers_db.py` — provider CRUD (create, update, delete, toggle, list).
- `storage/combos_db.py` — combo CRUD.
- `storage/pricing_db.py` — pricing override CRUD.
- CLI commands call `init_db()` inline before DB operations (see pattern in `cli.py`). Follow this if adding CLI subcommands that touch the DB.

## Pricing & budget enforcement

- `PricingRegistry` merges builtin defaults (~28 models in `pricing/builtin.py`) with DB overrides from the `pricing_overrides` table (seeded from YAML `pricing:` section on first startup). Cost computed at recording time via `compute_cost(usage, model, registry)`. Unknown models cost $0.0 (not an error).
- Budgets are daily spending limits in the `budgets` SQLite table. Per-key (`key_id` set) or global (`key_id = NULL`). Enforcement in `_handle()` before routing: warn at 80% (request proceeds), block at 100% (request rejected with `429` + `Retry-After`). Most restrictive wins. Fail-safe: DB errors don't block requests.
- CLI: `janus budgets list/set/delete`, `janus pricing list/show`.

## Config

Runtime config is YAML at `~/.janus/config.yaml` with `${ENV_VAR}` token resolution. Generate a template with `janus config-init`. After first startup, config is seeded into SQLite and the DB is authoritative (see "DB-driven config" above). The `providers:`, `combos:`, and `token_savers:` keys can be null — the loader filters None values.

Combos are named ordered model sequences. A client sends `"model": "combo-name"` and Janus tries each model in order with all its accounts.

## Documentation & packaging

Docs site uses MkDocs Material. Config in `mkdocs.yml`, pages in `docs/`. Internal design specs in `docs/superpowers/` are excluded from the site nav via `exclude_docs`. Preview with `mkdocs serve`, verify with `mkdocs build --strict`.

Build backend is hatchling. Wheel + sdist via `python -m build`. PyPI publishing is automated via `.github/workflows/publish.yml` (OIDC trusted publisher, triggered on `v*` tag push). GitHub Pages deployment via `.github/workflows/docs.yml` (triggered on push to `main` when `docs/`, `mkdocs.yml`, or `README.md` change).
