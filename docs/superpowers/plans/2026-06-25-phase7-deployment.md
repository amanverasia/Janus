# Phase 7: Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Janus production-ready with connection pooling, Docker containerization, CI/CD pipeline, and comprehensive documentation.

**Architecture:** Provider classes switch from per-request `httpx.AsyncClient` to shared clients with pool limits, cached in `app.state` and closed on shutdown. Docker multi-stage build ships a slim image. GitHub Actions runs the full test suite on every push.

**Tech Stack:** Python 3.11, FastAPI, httpx, Docker, GitHub Actions

---

## Task 1: Add `close()` to Provider Protocol

**Files:**
- Modify: `src/janus/providers/base.py`

- [ ] **Step 1: Update the Provider protocol**

Replace the entire contents of `src/janus/providers/base.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RawResult:
    status_code: int
    json_data: dict[str, Any] | None = None
    lines: AsyncIterator[str] | None = None


class Provider(Protocol):
    name: str

    async def call(self, payload: dict[str, Any], stream: bool) -> RawResult: ...

    async def close(self) -> None: ...
```

- [ ] **Step 2: Verify nothing breaks**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All 178 tests pass (protocol change is additive)

- [ ] **Step 3: Commit**

```bash
git add src/janus/providers/base.py
git commit -m "feat: add close() to Provider protocol"
```

---

## Task 2: Shared Client in OpenAICompatProvider

**Files:**
- Modify: `src/janus/providers/openai_compat.py`
- Test: `tests/unit/providers/test_providers.py`

- [ ] **Step 1: Write the failing test for close()**

Add this test to the end of `tests/unit/providers/test_providers.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_close():
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_reuses_client():
    call_count = 0
    original_init = httpx.AsyncClient.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        original_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = counting_init
    try:
        provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
        respx.post("https://test.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
        )
        await provider.call({"model": "m1", "messages": []}, stream=False)
        await provider.call({"model": "m1", "messages": []}, stream=False)
        assert call_count == 1, f"Expected 1 client init, got {call_count}"
        await provider.close()
    finally:
        httpx.AsyncClient.__init__ = original_init
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_providers.py::test_openai_compat_provider_reuses_client -v`
Expected: FAIL (client created per-request, count > 1)

- [ ] **Step 3: Rewrite OpenAICompatProvider with shared client**

Replace the entire contents of `src/janus/providers/openai_compat.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        url = f"{self.base_url}/chat/completions"
        if stream:
            return await self._call_stream(url, payload)
        r = await self._client.post(url, json=payload, headers=self._headers)
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}

        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST", url, json=payload, headers=self._headers
            ) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run all provider tests**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_providers.py -v`
Expected: PASS (6 tests including new ones)

- [ ] **Step 5: Commit**

```bash
git add src/janus/providers/openai_compat.py tests/unit/providers/test_providers.py
git commit -m "feat: shared httpx client in OpenAICompatProvider"
```

---

## Task 3: Shared Client in AnthropicProvider

**Files:**
- Modify: `src/janus/providers/anthropic.py`

- [ ] **Step 1: Rewrite AnthropicProvider with shared client**

Replace the entire contents of `src/janus/providers/anthropic.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, api_key: str, base_url: str = "https://api.anthropic.com"
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        url = f"{self.base_url}/v1/messages"
        if stream:
            return await self._call_stream(url, payload)
        r = await self._client.post(url, json=payload, headers=self._headers)
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}

        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST", url, json=payload, headers=self._headers
            ) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 2: Run all provider tests**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_providers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 3: Commit**

```bash
git add src/janus/providers/anthropic.py
git commit -m "feat: shared httpx client in AnthropicProvider"
```

---

## Task 4: Shared Client in GeminiProvider

**Files:**
- Modify: `src/janus/providers/gemini.py`

- [ ] **Step 1: Rewrite GeminiProvider with shared client**

Replace the entire contents of `src/janus/providers/gemini.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        model = payload.get("model", "gemini-2.0-flash")
        model = (
            model.removeprefix("models/")
            if isinstance(model, str)
            else "gemini-2.0-flash"
        )
        if stream:
            url = (
                f"{self.base_url}/v1beta/models/{model}:streamGenerateContent"
                f"?alt=sse&key={self.api_key}"
            )
            return await self._call_stream(url, payload)
        url = (
            f"{self.base_url}/v1beta/models/{model}:generateContent"
            f"?key={self.api_key}"
        )
        r = await self._client.post(
            url, json=payload, headers={"Content-Type": "application/json"}
        )
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 2: Run all provider tests**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_providers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 3: Commit**

```bash
git add src/janus/providers/gemini.py
git commit -m "feat: shared httpx client in GeminiProvider"
```

---

## Task 5: Provider Cache in App State + Lifecycle

**Files:**
- Modify: `src/janus/api/routes.py`
- Modify: `src/janus/app.py`

- [ ] **Step 1: Remove `_build_provider()` from routes.py and use cached providers**

Read `src/janus/api/routes.py`. In the `_handle()` function, the provider is currently built per-request:

```python
        provider = _build_provider(target.provider_config)
```

Replace that line with:

```python
        providers: dict[str, Provider] = request.app.state.providers
        provider = providers[target.provider_config.id]
```

Remove the `_build_provider()` function entirely from routes.py. Also remove the now-unused imports for the provider classes at the top of the file:

```python
from janus.providers.anthropic import AnthropicProvider
from janus.providers.base import Provider
from janus.providers.gemini import GeminiProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
```

Replace these with a single import:

```python
from janus.providers.base import Provider
```

Keep the `ProviderConfig` import — it's still needed by other code.

- [ ] **Step 2: Build provider cache in app.py**

Read `src/janus/app.py`. Add a `_build_providers()` helper function and wire it into `create_app()`.

Add these imports at the top:

```python
from janus.providers.anthropic import AnthropicProvider
from janus.providers.base import Provider
from janus.providers.gemini import GeminiProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
```

Add this function before `lifespan`:

```python
def _build_provider(config: ProviderConfig) -> Provider:
    if config.api_type == "opencode_free":
        return OpenCodeFreeProvider()
    if config.api_type == "openai_compat":
        return OpenAICompatProvider(base_url=config.base_url, api_key=config.api_key)
    if config.api_type == "anthropic":
        return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "gemini":
        return GeminiProvider(api_key=config.api_key or "")
    raise ValueError(f"Unknown api_type: {config.api_type}")
```

Add `from janus.config.schema import JanusConfig, ProviderConfig` (update existing import).

In `create_app()`, after the `app.state.pricing_registry = ...` line, add:

```python
    providers: dict[str, Provider] = {}
    for pc in config.providers:
        providers[pc.id] = _build_provider(pc)
    app.state.providers = providers
```

Update `lifespan` to close providers on shutdown. Replace the existing lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    await init_db(db_path)
    yield
    for provider in app.state.providers.values():
        await provider.close()
```

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All 178 tests pass

- [ ] **Step 4: Run lint and typecheck**

Run: `.venv/bin/ruff check src/janus/ tests/` and `.venv/bin/mypy src/janus/`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py src/janus/app.py
git commit -m "feat: cache providers in app state with lifecycle management"
```

---

## Task 6: .dockerignore

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Create .dockerignore**

Create `.dockerignore` in the repo root:

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
janus-data/
```

- [ ] **Step 2: Commit**

```bash
git add .dockerignore
git commit -m "feat: add .dockerignore"
```

---

## Task 7: Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create multi-stage Dockerfile**

Create `Dockerfile` in the repo root:

```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml src/ ./
RUN pip install --no-cache-dir . && pip wheel . --no-deps -o /wheels

FROM python:3.11-slim
RUN useradd -m -s /bin/bash janus
WORKDIR /app
COPY --from=builder /wheels/*.whl /tmp/
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER janus
EXPOSE 20128
CMD ["janus", "serve", "--host", "0.0.0.0", "--port", "20128", "--config", "/home/janus/.janus/config.yaml"]
```

- [ ] **Step 2: Verify Docker build works**

Run: `docker build -t janus-test .`
Expected: Build succeeds without errors

- [ ] **Step 3: Verify the container starts and responds**

Run: `docker run --rm -d --name janus-test -p 20128:20128 janus-test && sleep 3 && curl -s http://localhost:20128/v1/health && docker stop janus-test`
Expected: `{"status":"ok"}`

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: multi-stage Dockerfile"
```

---

## Task 8: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

Create `docker-compose.yml` in the repo root:

```yaml
services:
  janus:
    build: .
    ports:
      - "20128:20128"
    volumes:
      - ./janus-data:/home/janus/.janus
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - GEMINI_API_KEY=${GEMINI_API_KEY:-}
      - GLM_API_KEY=${GLM_API_KEY:-}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
    restart: unless-stopped
```

- [ ] **Step 2: Verify compose config is valid**

Run: `docker compose config`
Expected: Valid YAML output, no errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-compose.yml with volume and env passthrough"
```

---

## Task 9: GitHub Actions CI Workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: ["*"]
  pull_request:
    branches: ["main"]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Ruff check
        run: ruff check src/janus/ tests/

      - name: Ruff format check
        run: ruff format --check src/janus/ tests/

      - name: Mypy
        run: mypy src/janus/

      - name: Pytest
        run: python -m pytest -q
```

- [ ] **Step 2: Commit**

```bash
mkdir -p .github/workflows
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions workflow (test + lint + typecheck)"
```

---

## Task 10: README Rewrite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README**

Replace the entire contents of `README.md`:

```markdown
# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, ...) talk to, then translates and routes each request to
40+ AI providers — without either side needing to know the other exists.

## Quickstart

```bash
# Install
pip install -e .

# Generate default config
janus config-init

# Start the server
janus serve --port 20128
```

Point your coding tool at `http://localhost:20128/v1` and start routing.

## Docker

```bash
# Create config in the persistent volume
mkdir -p janus-data
cp ~/.janus/config.yaml janus-data/config.yaml  # or create one with janus config-init

# Build and run
docker compose up -d
```

The SQLite database and config persist in `./janus-data/`. Environment variables
from `.env` are passed through for `${ENV_VAR}` resolution in config.

## Configuration

Janus reads YAML config from `~/.janus/config.yaml` with `${ENV_VAR}` token
resolution. Generate a template with `janus config-init`.

```yaml
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false

providers:
  - id: openai
    prefix: openai
    api_type: openai_compat
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini, o3, o4-mini]

  - id: anthropic
    prefix: anthropic
    api_type: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-4-20250514, claude-opus-4-20250514]

combos:
  - name: best-effort
    models: [anthropic/claude-sonnet-4-20250514, openai/gpt-4o]
```

### Supported Provider Types

| `api_type` | Use For |
|---|---|
| `openai_compat` | Any OpenAI-compatible API (OpenAI, Groq, Together, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, ...) |
| `anthropic` | Direct Anthropic API |
| `gemini` | Direct Google Gemini API |
| `opencode_free` | OpenCode Zen free tier |

### Known Provider Base URLs

| Provider | `base_url` |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Together AI | `https://api.together.xyz/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| Fireworks | `https://api.fireworks.ai/inference/v1` |
| Perplexity | `https://api.perplexity.ai` |
| xAI (Grok) | `https://api.x.ai/v1` |
| Qwen/DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

## Client Setup

Point your coding tools at Janus:

**Claude Code / Anthropic tools:**
```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
```

**OpenAI tools (Codex, Cursor, etc.):**
```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
export OPENAI_API_KEY=sk-janus-yourkey  # if require_api_key is on
```

## Features

- **Fallback routing** — multi-account rotation with cooldowns (429->60s, 5xx->30s, auth->300s, network->15s)
- **Combos** — named ordered model sequences (e.g., `"model": "best-effort"`)
- **Token savers** — RTK compression (default ON), Caveman terse prompt, Ponytail lazy-dev prompt
- **Budgets** — daily spending limits per API key or global, with warn/block thresholds
- **Analytics** — cost tracking, spend trends, success rates, per-model/provider/key breakdowns
- **Pricing** — 28 builtin model prices, YAML-overridable, cache token rates
- **Dashboard** — HTMX UI at `/dashboard` with charts, budget management, usage stats

## CLI Reference

| Command | Description |
|---|---|
| `janus serve` | Start the gateway server |
| `janus config-init` | Generate default config YAML |
| `janus config-path` | Print config file path |
| `janus keys create/list/revoke` | Manage API keys |
| `janus usage stats/cost/by-key` | Usage and cost reports |
| `janus budgets list/set/delete` | Manage spending budgets |
| `janus pricing list/show` | View model pricing |

## Development

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
python -m venv .venv
pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest

# Lint + typecheck
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/

# Start dev server
.venv/bin/janus serve --port 20128 --reload
```

## Tech Stack

Python 3.11+ / FastAPI / httpx / Pydantic v2 / aiosqlite / Jinja2 / HTMX / Chart.js

## License

TBD
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: comprehensive README rewrite"
```

---

## Task 11: Final Verification + Push

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: All 180 tests pass (178 existing + 2 new provider tests)

- [ ] **Step 2: Run lint + typecheck**

Run: `.venv/bin/ruff check src/janus/ tests/ && .venv/bin/mypy src/janus/`
Expected: Clean

- [ ] **Step 3: Run ruff format check**

Run: `.venv/bin/ruff format --check src/janus/ tests/`
Expected: Clean

- [ ] **Step 4: Verify Docker build**

Run: `docker build -t janus-test . && docker run --rm janus-test janus --help`
Expected: CLI help output

- [ ] **Step 5: Push to remote**

```bash
git push origin main
```
