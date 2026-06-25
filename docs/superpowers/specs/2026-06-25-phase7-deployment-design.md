# Phase 7: Deployment

## Overview

Make Janus production-ready: Docker containerization with docker-compose, connection pooling for provider HTTP clients, CI/CD pipeline via GitHub Actions, and comprehensive README documentation.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| New providers | Skip Azure + Bedrock | Azure needs deployment-name config, Bedrock needs boto3/SigV4. Most providers already work via `openai_compat`. |
| Connection pooling | Shared client per provider | Standard production pattern. Avoids repeated TLS handshakes. Lifecycle managed via FastAPI lifespan. |
| Docker | Multi-stage Dockerfile + compose | Smaller image (~100MB), volume for SQLite persistence. |
| CI/CD | Test + lint + typecheck only | Fast feedback, no Docker build complexity yet. |
| Gemini client endpoint | Not included | Adapter exists for upstream use; client-facing route deferred. |

## 1. Connection Pooling

### Problem

Each provider class (`OpenAICompatProvider`, `AnthropicProvider`, `GeminiProvider`, `OpenCodeFreeProvider`) creates a new `httpx.AsyncClient` inside its `call()` method per request. Every request pays the full TCP/TLS connection setup cost. Additionally, `_build_provider()` in `api/routes.py` constructs a fresh provider instance for every request.

### Design

**Provider classes:** Move `httpx.AsyncClient` to an instance attribute created in `__init__`. Configure pool limits:

```python
self._client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0),
)
```

Add `async def close(self) -> None` to each provider and to the `Provider` protocol in `base.py`.

**Provider caching in app state:** Instead of `_build_provider()` creating a new provider per request, build all providers once in `create_app()` and cache them in `app.state.providers` as `dict[str, Provider]` keyed by `config.id`. The `_handle()` function looks up the cached provider by `target.provider_config.id`.

**Lifecycle management:** The FastAPI lifespan handler in `app.py` closes all cached providers on shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    await init_db(db_path)
    yield
    for provider in app.state.providers.values():
        await provider.close()
```

**Files modified:**
- `src/janus/providers/base.py` — add `close()` to Provider protocol
- `src/janus/providers/openai_compat.py` — shared client in `__init__`, remove per-request client creation
- `src/janus/providers/anthropic.py` — same
- `src/janus/providers/gemini.py` — same
- `src/janus/providers/opencode_free.py` — inherits from OpenAICompat, no change needed
- `src/janus/api/routes.py` — remove `_build_provider()`, use cached providers from `app.state`
- `src/janus/app.py` — build provider cache in `create_app()`, close on shutdown in `lifespan`

## 2. Docker

### Dockerfile (multi-stage)

```dockerfile
# Builder stage
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml src/ ./
RUN pip install --no-cache-dir . && pip wheel . --no-deps -o /wheels

# Runtime stage
FROM python:3.11-slim
RUN useradd -m -s /bin/bash janus
WORKDIR /app
COPY --from=builder /wheels/*.whl /tmp/
COPY pyproject.toml ./
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER janus
EXPOSE 20128
CMD ["janus", "serve", "--host", "0.0.0.0", "--port", "20128"]
```

- Builder stage installs dependencies and builds the wheel. Runtime stage installs only the wheel (pulling deps from builder's pip cache).
- Non-root user `janus` for security.
- Data directory defaults to `~/.janus` (for the `janus` user, this is `/home/janus/.janus`).

### docker-compose.yml

```yaml
services:
  janus:
    build: .
    ports:
      - "20128:20128"
    volumes:
      - ./janus-data:/home/janus/.janus
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    restart: unless-stopped
```

- Volume `./janus-data` persists SQLite DB + config across container restarts.
- Environment variables passed through for `${ENV_VAR}` config resolution.
- Users place `config.yaml` in `./janus-data/` and override the config path if needed.

### .dockerignore

```
.venv/
.git/
__pycache__/
*.pyc
tests/
docs/
.mypy_cache/
.ruff_cache/
*.egg-info/
```

## 3. CI/CD Pipeline

### `.github/workflows/ci.yml`

Triggers on every push and pull request. Single job on `ubuntu-latest` with Python 3.11.

Steps (sequential, fail-fast):
1. Checkout code
2. Set up Python 3.11
3. `pip install -e ".[dev]"`
4. `ruff check src/janus/ tests/`
5. `ruff format --check src/janus/ tests/`
6. `mypy src/janus/`
7. `python -m pytest -q`

No caching, no matrix, no Docker build. Fast feedback (~30s).

## 4. README + Documentation

### Structure

1. **Header** — project name, one-line description, badges (build status, Python version)
2. **Quickstart** — pip install, config-init, serve (3 steps)
3. **Docker** — docker compose up, volume explanation, config file placement
4. **Configuration** — YAML overview with `${ENV_VAR}` resolution, provider config examples (OpenAI, Anthropic, Gemini, openai_compat), combo example
5. **Provider Setup** — table of known-working providers with `base_url` values (Groq, Together, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, etc.)
6. **Client Setup** — how to point coding tools at Janus (set base URL to `http://localhost:20128/v1`, API key)
7. **Features** — fallback routing, combos, token savers, budgets, analytics dashboard, pricing
8. **CLI Reference** — table of all `janus` commands
9. **Development** — clone, venv, pip install -e ".[dev]", pytest, ruff, mypy
10. **License** — if applicable

## 5. Testing Strategy

- **Connection pooling unit tests:** Update existing provider tests to verify `close()` is called, verify the client is reused (not recreated per call). Mock `httpx.AsyncClient` to assert it's only instantiated once.
- **Integration tests:** Verify existing API tests still pass with cached providers. The `create_app()` change is backward-compatible — tests that call `create_app()` with providers will get cached providers automatically.
- **Docker:** Manual verification (build image, run container, hit health endpoint). Not automated in this phase.
- **CI/CD:** The workflow itself is the test — it runs the full suite on every push.

## 6. File Map

### New Files

```
Dockerfile
docker-compose.yml
.dockerignore
.github/workflows/ci.yml
```

### Modified Files

```
src/janus/providers/base.py         — add close() to Provider protocol
src/janus/providers/openai_compat.py — shared client, close()
src/janus/providers/anthropic.py    — shared client, close()
src/janus/providers/gemini.py       — shared client, close()
src/janus/api/routes.py             — remove _build_provider(), use app.state.providers
src/janus/app.py                    — build provider cache, close on shutdown
README.md                           — comprehensive rewrite
```

### Unchanged

No changes to `canonical/`, `formats/`, `config/`, `storage/`, `dashboard/`, `pricing/`, `routing/`, `streaming/`, `tokensavers/`, or `cli.py`.
