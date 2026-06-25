# Contributing to Janus

Thanks for your interest in contributing! This guide covers setup, development workflows, and how to extend Janus.

## Development Setup

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
python -m venv .venv
pip install -e ".[dev]"
```

## Daily Commands

```bash
# Run all tests (never use bare 'pytest')
.venv/bin/python -m pytest

# Run a single test
.venv/bin/python -m pytest tests/unit/formats/test_openai.py::test_name -v

# Lint
.venv/bin/ruff check src/janus/ tests/

# Format check
.venv/bin/ruff format --check src/janus/ tests/

# Typecheck
.venv/bin/mypy src/janus/

# Start dev server
.venv/bin/janus serve --port 20128 --reload

# Preview docs locally
.venv/bin/mkdocs serve
```

Run `ruff check`, `ruff format --check`, and `mypy` before every commit. CI enforces all three.

## Architecture Constraint

Janus uses a **canonical intermediate model**. The rule is simple:

> `formats/` and `providers/` never import or call each other — they only talk to `canonical/`.

This gives 2N adapters instead of N² translators. **Do not break this boundary.** If you need a format to talk to a provider, you're doing it wrong — go through the canonical model.

### Request Flow

```
client format → parse_request → CanonicalRequest → SaverPipeline.apply
→ budget check → FallbackHandler.resolve_attempts
→ per-attempt: build_upstream_request → upstream call → parse_upstream_response
→ CanonicalResponse → emit_response → record_usage (with cost)
```

On 429/5xx/auth/network errors, the account is cooled down and the next attempt is tried.

## Adding a New Format Adapter

1. Create `src/janus/formats/<name>.py` implementing all six methods: `parse_request`, `build_upstream_request`, `parse_upstream_response`, `emit_response`, `stream_parser`, `stream_emitter`.
2. Register in the `FORMATS` dict in `src/janus/api/routes.py`.

## Adding a New Provider Executor

1. Create `src/janus/providers/<name>.py` with an `async call(payload, stream) -> RawResult` method and an `async close()` method.
2. Add a case to `_build_provider()` in `src/janus/app.py`.
3. If the provider's native format differs from its `api_type`, update `_resolve_format()` in `src/janus/api/routes.py`.

## Adding a New Token Saver

1. Implement the `TokenSaver` protocol (`transform(req) -> CanonicalRequest`) in `src/janus/tokensavers/`.
2. Add to pipeline construction in `src/janus/app.py`.
3. Savers must be fail-safe — exceptions are caught by the pipeline and logged, never breaking the request.

## PR Process

- Squash-merge PRs to `main`. Branches are deleted after merge.
- Write tests for all new functionality. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.
- Provider tests mock httpx with `respx` — no real network calls.
- Integration tests use FastAPI ASGI transport (`httpx.ASGITransport`) in-process.
- Test fixtures live in `tests/fixtures/`.
- No code comments unless explicitly requested.
- `ruff` with line-length 100, rules: E, F, I, N, W, UP.
- `mypy --strict` — bare `dict`/`list` must be typed. Use `X | Y` not `Union`. Use `StrEnum` not `str, Enum`.
