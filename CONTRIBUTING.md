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

## CI Timing and Recovery

PRs and pushes to `main` run the reusable check suite. Ruff, formatting, mypy,
dashboard validation, migration smoke, and docs run once. Tests and the 80% line
coverage gate run on both Python 3.11 and 3.12. Browser and packaging checks run
alongside them. A new PR commit cancels superseded CI and Docker runs; main and
release runs are not interrupted. Release tags run one shared check suite before
PyPI and Docker publishing. PyPI uploads the distributions validated by that suite.

Healthy Python test jobs currently take about five to six minutes. CI prints each
test name and the 30 slowest test durations. A test taking 60 seconds dumps thread
stacks; the 120-second timeout includes fixture setup and teardown and terminates
the process. A separate nine-minute process limit also catches collection or
session-shutdown hangs and dumps stacks before termination. The Python job has a
12-minute limit, leaving time to upload diagnostics after the process exits.

For a failed or timed-out test job, download the
`test-diagnostics-<python-version>-<attempt>` artifact from the Actions run. It
contains `pytest.log` with test names and thread stacks, plus `junit.xml` when pytest
finishes normally. Coverage reports are uploaded separately when available. Hard
timeouts can prevent JUnit and coverage reports from being finalized; the saved
log is the primary diagnostic. Test diagnostics are retained for 14 days.

Reproduce the test settings locally with:

```bash
.venv/bin/python -X faulthandler -m pytest -vv \
  --timeout=120 --timeout-method=thread -o faulthandler_timeout=60 \
  --durations=30 --cov=src/janus --cov-fail-under=80
```

Inspect the log and identify the stuck test or cleanup hook before retrying. A
passing retry does not establish that a hang was caused by runner contention.
SQLite worker stacks can reflect idle connections; inspect the main thread,
pending async work, and pool cleanup too.

The shared test fixture closes database pools after every test, before its event
loop is torn down. Keep this cleanup global: ASGITransport does not execute the
application lifespan shutdown, so leaving connections until session completion
retains SQLite worker threads and pools from earlier tests.

If a run must be cancelled manually:

```bash
gh run cancel <run-id>
gh run view <run-id> --json status,conclusion
# Wait until status is completed, then retry failed/cancelled jobs.
gh run rerun <run-id> --failed
```

GitHub can reject a rerun while cancellation is still settling. Wait for the
terminal status rather than repeatedly cancelling or retrying an active run.

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
2. Add construction logic to `reload_savers()` in `src/janus/dashboard/reload.py`.
3. Savers must be fail-safe — exceptions are caught by the pipeline and logged, never breaking the request.

## Maintainer Scripts

The `scripts/` directory holds one-off maintenance tools. They are not installed with the
package — run them from a dev checkout with `pip install -e ".[dev]"` so `janus` imports resolve.

| Script | When to run |
|--------|-------------|
| `scripts/generate_model_catalog.py` | After updating the upstream model catalog in [Dashboard_For_Apis](https://github.com/amanverasia/Dashboard_For_Apis). Regenerates `src/janus/inventory/data/model_catalog.json`. Default source: sibling checkout at `../Dashboard_For_Apis/backend/src/services/model-catalog.ts`; override with `--source`. |
| `scripts/seed_openrouter_pricing.py` | When refreshing OpenRouter per-model pricing in your local DB. Fetches the live catalog and writes rows to `pricing_overrides`. Use `--dry-run` to preview counts. |

```bash
# Regenerate model catalog (custom source path)
.venv/bin/python scripts/generate_model_catalog.py \
  --source /path/to/model-catalog.ts

# Seed OpenRouter pricing into ~/.janus/janus.db
.venv/bin/python scripts/seed_openrouter_pricing.py

# Preview without writing
.venv/bin/python scripts/seed_openrouter_pricing.py --dry-run
```

## PR Process

- Squash-merge PRs to `main`. Branches are deleted after merge.
- Write tests for all new functionality. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.
- Provider tests mock httpx with `respx` — no real network calls.
- Integration tests use FastAPI ASGI transport (`httpx.ASGITransport`) in-process.
- Test fixtures live in `tests/fixtures/`.
- No code comments unless explicitly requested.
- `ruff` with line-length 100, rules: E, F, I, N, W, UP.
- `mypy --strict` — bare `dict`/`list` must be typed. Use `X | Y` not `Union`. Use `StrEnum` not `str, Enum`.
