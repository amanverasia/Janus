# AGENTS.md

## Dev environment

- Python 3.11 in a `.venv` at repo root. Always use `.venv/bin/python -m pytest`, not bare `pytest`.
- Install: `pip install -e ".[dev]"` (editable + dev extras including respx, ruff, mypy).

## Commands

```bash
# Run all tests
.venv/bin/python -m pytest

# Run a single test
.venv/bin/python -m pytest tests/unit/formats/test_openai.py::test_name -v

# Lint + typecheck (run both before committing)
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/

# Format check
.venv/bin/ruff format --check src/janus/ tests/

# Start dev server
.venv/bin/janus serve --port 20128 --config ~/.janus/config.yaml
```

## Architecture constraint

Janus uses a **canonical intermediate model**. The rule: `formats/` and `providers/` never import or call each other — they only talk to `canonical/`. This is intentional (2N adapters instead of N² translators). Do not break this boundary.

Request flow: client format → `parse_request` → `CanonicalRequest` → `FallbackHandler.resolve_attempts` → per-attempt: `build_upstream_request` → upstream call → `parse_upstream_response` → `CanonicalResponse` → `emit_response`. On 429/5xx/auth/network errors, the account is cooled down and the next attempt is tried.

## Routing & fallback layer

- `ProviderRegistry` stores `list[ProviderConfig]` per prefix (multi-account). `lookup()` returns `list[ResolvedTarget]`, not a single target.
- `FallbackHandler` (`routing/fallback.py`) expands combos → models → available accounts, filtering out cooled-down accounts. Cooldown durations: 429→60s, 5xx→30s, auth→300s, network→15s. State is in-memory (`time.monotonic()`).
- `routing/errors.py` has `classify_error(status_code)` and `is_fallback_eligible(error)` — these drive fallback decisions in `_handle()`.
- The retry loop lives in `api/routes.py::_handle()`. Streaming requests do NOT retry mid-stream (can't replay partial output).
- Adding multi-account: register multiple `ProviderConfig` entries with the same `prefix` but different `id`/`api_key`.

## Adding a new format adapter

1. Create `src/janus/formats/<name>.py` implementing all six methods: `parse_request`, `build_upstream_request`, `parse_upstream_response`, `emit_response`, `stream_parser`, `stream_emitter`.
2. Register in the `FORMATS` dict in `src/janus/api/routes.py`.

## Adding a new provider executor

1. Create `src/janus/providers/<name>.py` with a `call(payload, stream) -> RawResult` method.
2. Add a case to `_build_provider()` in `src/janus/api/routes.py`.
3. If the provider's native format differs from its `api_type`, update `_resolve_format()` in routes.py.

## Code style (enforced by tooling)

- `ruff` with line-length 100, rules: E, F, I, N, W, UP.
- `mypy --strict` — bare `dict`/`list` must be typed (`dict[str, Any]`). Use `X | Y` not `Union`. Use `StrEnum` not `str, Enum`.
- No code comments unless explicitly requested.
- src layout: package code lives under `src/janus/`, not repo root.

## Testing

- `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions work without `@pytest.mark.asyncio`.
- Provider tests mock httpx with `respx` (no real network calls).
- Integration tests use FastAPI ASGI transport (`httpx.ASGITransport`) in-process.
- Test fixtures (sample API payloads) live in `tests/fixtures/`.

## Token savers

The `tokensavers/` package runs on the canonical request after parsing, before provider routing. Each saver is a pure `transform(req) -> CanonicalRequest`. The pipeline (`tokensavers/pipeline.py`) runs enabled savers in sequence and is fail-safe — exceptions are caught and logged, never breaking the request.

- **RTK** (default ON) — compresses `tool_result` content parts (git diff, ls, grep, logs). Auto-detects format, strips ANSI/diff-mode/permissions, deduplicates, smart-truncates.
- **Caveman** — prepends a terse-output system prompt.
- **Ponytail** — prepends a lazy-dev system prompt (3 levels: lite/full/ultra).
- Config: `token_savers:` section in YAML. Savers stack (all enabled ones run in order).
- To add a new saver: implement `TokenSaver` protocol in `tokensavers/`, add to pipeline construction in `app.py`.

## SQLite storage

The `storage/` package manages runtime state in SQLite (`~/.janus/janus.db`). DB is auto-created on app startup via FastAPI lifespan (`app.py`).

- `storage/database.py` — `init_db()` + `get_connection()` (async context manager using `aiosqlite`).
- `storage/api_keys.py` — keys are `sk-janus-{32hex}`, stored as SHA256 hash. The API-key gate (`api/deps.py`) checks both config `api_keys` (static list) AND DB keys.
- `storage/usage.py` — records per-request token usage (fire-and-forget, non-streaming only). CLI: `janus usage stats`.
- CLI key management: `janus keys create/list/revoke`.

## Dashboard

The `dashboard/` package serves an HTMX + Jinja2 UI at `/dashboard`. No npm, no build step — Tailwind and HTMX via CDN. Templates are in `dashboard/templates/`. Management API endpoints (`POST /dashboard/api/keys`, `DELETE /dashboard/api/keys/{id}`) return HTMX partials, not JSON.

## Config

Runtime config is YAML at `~/.janus/config.yaml` with `${ENV_VAR}` token resolution. The `providers:`, `combos:`, and `token_savers:` keys can be null (all commented out) — the loader filters None values. Generate a template with `janus config-init`.

Combos are named ordered model sequences. A client sends `"model": "combo-name"` and Janus tries each model in order with all its accounts.
