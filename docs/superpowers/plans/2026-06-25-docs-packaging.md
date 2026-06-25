# Phase 8: Documentation & Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package Janus for PyPI publication with GPLv3 license, build a comprehensive MkDocs Material documentation site, and set up automated CI workflows for publishing and docs deployment.

**Architecture:** All deliverables are static files (markdown, YAML, CI workflows, LICENSE). No source code changes. Verification via `mkdocs build`, `python -m build`, and existing test suite.

**Tech Stack:** MkDocs Material, hatchling (build backend), GitHub Actions (OIDC PyPI publish + gh-pages deploy), Keep-a-Changelog format.

---

### Task 1: LICENSE file

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Download GPLv3 license text**

Run:
```bash
curl -sL https://www.gnu.org/licenses/gpl-3.0.txt -o LICENSE
```

- [ ] **Step 2: Verify the file exists and has content**

Run: `wc -l LICENSE`
Expected: ~675 lines

- [ ] **Step 3: Commit**

```bash
git add LICENSE
git commit -m "docs: add GPLv3 license"
```

---

### Task 2: pyproject.toml metadata

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add license, classifiers, URLs, and dev dependencies**

Add `license` field after `description` (line 8):
```toml
license = "GPL-3.0-only"
```

Add after line 9 (`requires-python`):
```toml
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: FastAPI",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Internet :: Proxy Servers",
]
```

Add URLs section after `python-multipart` dependency (before the closing `]` of dependencies, add a blank line then):
```toml
[project.urls]
Homepage = "https://github.com/amanverasia/Janus"
Documentation = "https://amanverasia.github.io/Janus/"
Repository = "https://github.com/amanverasia/Janus"
Issues = "https://github.com/amanverasia/Janus/issues"
```

Add to `[project.optional-dependencies] dev` list (after `"pytest-cov>=5.0"`):
```toml
    "mkdocs-material>=9.5",
    "build>=1.2",
```

- [ ] **Step 2: Verify build works**

Run: `pip install -e ".[dev]" && python -m build --no-isolation`
Expected: Creates `dist/janus-0.1.0-py3-none-any.whl` and `dist/janus-0.1.0.tar.gz`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add license, classifiers, URLs, mkdocs+build dev deps"
```

---

### Task 3: .gitignore additions

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add build artifact entries**

Append to `.gitignore`:
```
# MkDocs build output
site/

# Python build artifacts
dist/
*.egg-info/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore mkdocs site/ and python build artifacts"
```

---

### Task 4: CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write the changelog**

Create `CHANGELOG.md` with content:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-25

### Added
- Core routing gateway with canonical intermediate model translation
- Three format adapters: OpenAI, Anthropic, Gemini
- Four provider executors: openai_compat, anthropic, gemini, opencode_free
- SSE streaming translation between all supported formats
- Multi-account fallback routing with cooldowns (429→60s, 5xx→30s, auth→300s, network→15s)
- Named combos: ordered model sequences for automatic fallback chains
- Token savers: RTK compression (default ON), Caveman terse prompt, Ponytail lazy-dev prompt (3 levels)
- SQLite persistence: API keys (SHA256-hashed), usage tracking, budget storage
- Pricing engine: 28 builtin model prices, YAML-overridable, progressive prefix matching
- Budget enforcement: per-key and global daily limits with warn (80%) and block (100%) thresholds
- Analytics: cost tracking, spend trends, success rates, per-model/provider/key breakdowns
- HTMX dashboard with 7 pages: Overview, Providers, Combos, API Keys, Usage, Analytics, Budgets
- CLI: serve, config-init, config-path, keys (create/list/revoke), usage (stats/cost/by-key), budgets (list/set/delete), pricing (list/show)
- Docker support: multi-stage build, docker-compose with volume persistence
- GitHub Actions CI: ruff check + format, mypy --strict, pytest
- Connection pooling: shared httpx.AsyncClient per provider (100 connections, 20 keepalive)
- Provider caching in app.state.providers keyed by config.id
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md with v0.1.0 release notes"
```

---

### Task 5: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write the contributing guide**

Create `CONTRIBUTING.md` with content:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md"
```

---

### Task 6: mkdocs.yml

**Files:**
- Create: `mkdocs.yml`

- [ ] **Step 1: Write mkdocs config**

Create `mkdocs.yml`:

```yaml
site_name: Janus
site_description: The two-faced AI routing gateway
site_url: https://amanverasia.github.io/Janus/
repo_url: https://github.com/amanverasia/Janus
repo_name: amanverasia/Janus
edit_uri: edit/main/docs/

theme:
  name: material
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - search.suggest
    - content.code.copy
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      toggle:
        icon: material/brightness-4
        name: Switch to light mode

nav:
  - Getting Started:
      - Overview: index.md
      - Getting Started: getting-started.md
      - Client Setup: client-setup.md
  - Guides:
      - Providers: providers.md
      - Combos & Fallback: combos.md
      - Token Savers: token-savers.md
      - Budgets: budgets.md
      - Dashboard: dashboard.md
  - Reference:
      - Configuration: configuration.md
      - API Reference: api-reference.md
      - CLI: cli.md
  - Development:
      - Architecture: architecture.md
      - Contributing: contributing.md

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - toc:
      permalink: true

plugins:
  - search

exclude_docs: |
  superpowers/
```

- [ ] **Step 2: Verify mkdocs config parses**

Run: `pip install -e ".[dev]" && mkdocs build --strict`
Expected: Site builds successfully (may warn about missing pages until docs are written, which is fine — build just creates the `site/` directory)

- [ ] **Step 3: Commit**

```bash
git add mkdocs.yml
git commit -m "docs: add mkdocs.yml Material config"
```

---

### Task 7: docs/index.md

**Files:**
- Create: `docs/index.md`

- [ ] **Step 1: Write the landing page**

Create `docs/index.md`:

````markdown
# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, ...) talk to, then translates and routes each request to
40+ AI providers — without either side needing to know the other exists.

## Why Janus?

- **One endpoint, every provider** — point your tools at a single URL, route to OpenAI, Anthropic, Gemini, Groq, DeepSeek, and more
- **Automatic fallback** — if one provider is rate-limited or down, Janus rotates to the next automatically
- **Cost tracking** — per-request cost estimation with 28 builtin model prices and budget enforcement
- **Token savings** — RTK compression strips boilerplate from tool outputs before sending to the model
- **No cloud, no telemetry** — runs entirely on your machine, your keys never leave your system

## Quick Start

```bash
pip install janus
janus config-init
janus serve --port 20128
```

Point your coding tool at `http://localhost:20128/v1` and start routing.

!!! tip "What next?"
    - [Getting Started](getting-started.md) — full install and first-request walkthrough
    - [Configuration](configuration.md) — YAML config reference
    - [Providers](providers.md) — setup guides for all supported providers
    - [Client Setup](client-setup.md) — connect Claude Code, Codex, Cursor, and more

## Features

### Fallback Routing

Multi-account rotation with intelligent cooldowns. When a provider returns 429, 5xx, auth, or network errors, Janus automatically tries the next available account or model.

### Combos

Named ordered model sequences. A client sends `"model": "best-effort"` and Janus tries each model in the combo chain with all its accounts.

### Token Savers

- **RTK** (default ON) — compresses tool output (git diffs, file listings, logs) before sending
- **Caveman** — prepends a brevity-maximizing system prompt
- **Ponytail** — prepends a lazy-developer prompt (3 levels: lite, full, ultra)

### Budgets

Daily spending limits per API key or global. Warn at 80%, block at 100% with `429 + Retry-After`. Managed via CLI or dashboard.

### Analytics

Cost tracking, spend trends, success rates, and breakdowns by model, provider, account, or client key. Visualized in the dashboard with Chart.js.

### Dashboard

HTMX-powered dark-themed UI at `/dashboard`. Seven pages: Overview, Providers, Combos, API Keys, Usage, Analytics, Budgets.

## Tech Stack

Python 3.11+ / FastAPI / httpx / Pydantic v2 / aiosqlite / Jinja2 / HTMX / Chart.js

## License

Janus is licensed under the [GPL-3.0](https://github.com/amanverasia/Janus/blob/main/LICENSE) license.
````

- [ ] **Step 2: Commit**

```bash
git add docs/index.md
git commit -m "docs: write landing page (index.md)"
```

---

### Task 8: docs/getting-started.md and docs/client-setup.md

**Files:**
- Create: `docs/getting-started.md`
- Create: `docs/client-setup.md`

- [ ] **Step 1: Write getting-started.md**

Create `docs/getting-started.md`:

````markdown
# Getting Started

## Installation

### pip

```bash
pip install janus
```

### Docker

```bash
docker pull ghcr.io/amanverasia/janus:latest
# Or build from source
git clone https://github.com/amanverasia/Janus.git
cd Janus
docker compose up -d
```

### From source (development)

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
python -m venv .venv
pip install -e ".[dev]"
```

## Configuration

Generate a default config file:

```bash
janus config-init
```

This creates `~/.janus/config.yaml`. Edit it to add your API keys:

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
```

Environment variables in `${VAR}` format are resolved at startup. Set them in your shell or `.env` file:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

See [Configuration](configuration.md) for the full YAML reference.

## Start the Server

```bash
janus serve --port 20128
```

Verify it's running:

```bash
curl http://localhost:20128/v1/health
# {"status": "ok"}
```

## Your First Request

Send an OpenAI-format request to Janus:

```bash
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

Janus translates this to the provider's native format, routes it, and returns the response in OpenAI format.

Use the Anthropic format too:

```bash
curl http://localhost:20128/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anthropic/claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

## List Available Models

```bash
curl http://localhost:20128/v1/models
```

Returns all registered provider models and combos.

## Next Steps

- [Client Setup](client-setup.md) — connect your coding tools
- [Providers](providers.md) — configure specific providers
- [Combos](combos.md) — set up fallback chains
- [Dashboard](dashboard.md) — explore the web UI at `/dashboard`
````

- [ ] **Step 2: Write client-setup.md**

Create `docs/client-setup.md`:

````markdown
# Client Setup

Point your coding tools at Janus. The base URL is always `http://localhost:20128/v1` (adjust port if you changed it).

## Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled in your config:

```bash
export ANTHROPIC_API_KEY=sk-janus-yourkey
```

Claude Code sends Anthropic-format requests. Janus translates and routes them to any configured provider.

## OpenAI-Compatible Tools (Codex, Cursor, etc.)

```bash
export OPENAI_BASE_URL=http://localhost:20128/v1
```

If `require_api_key` is enabled:

```bash
export OPENAI_API_KEY=sk-janus-yourkey
```

Tools send OpenAI-format requests to `POST /v1/chat/completions`. Janus translates and routes.

## Cline (VS Code)

In Cline settings:

1. Set **API Provider** to "OpenAI Compatible"
2. Set **Base URL** to `http://localhost:20128/v1`
3. Set **API Key** to your Janus key (or any value if `require_api_key` is off)

## Generic OpenAI-Compatible Clients

Any tool that accepts a custom OpenAI base URL works with Janus:

- **Base URL:** `http://localhost:20128/v1`
- **API Key:** Your Janus key (if auth enabled)
- **Model:** Use `prefix/model` format (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-20250514`) or a combo name

## Creating an API Key

```bash
janus keys create --name "my-key"
# Created key: sk-janus-a1b2c3d4...
# ID: 1  Name: my-key
```

The full key is shown once. Use it in the `Authorization: Bearer <key>` header or as your `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.

## Model Naming

Models are referenced as `{prefix}/{model}`:

| You send | Janus routes to |
|---|---|
| `openai/gpt-4o` | OpenAI provider, model `gpt-4o` |
| `anthropic/claude-sonnet-4-20250514` | Anthropic provider, model `claude-sonnet-4-20250514` |
| `gemini/gemini-2.5-pro` | Gemini provider, model `gemini-2.5-pro` |
| `best-effort` | Combo — tries each model in the combo chain |

See [Combos](combos.md) for fallback chain configuration.
````

- [ ] **Step 3: Commit**

```bash
git add docs/getting-started.md docs/client-setup.md
git commit -m "docs: write getting-started and client-setup pages"
```

---

### Task 9: docs/configuration.md

**Files:**
- Create: `docs/configuration.md`

- [ ] **Step 1: Write the configuration reference**

Create `docs/configuration.md` with full YAML reference. Include all config sections (`server`, `providers`, `combos`, `api_keys`, `token_savers`, `pricing`) with every field, type, default, and description. Include example YAML for each section. Use the config schema details from the codebase exploration (see the spec's config section for field details).

The page should cover:

- **Config file location**: `~/.janus/config.yaml` (print with `janus config-path`)
- **Environment variables**: `${VAR}` resolution, regex pattern
- **server section**: `port` (int, 20128), `host` (str, "127.0.0.1"), `require_api_key` (bool, false), `data_dir` (Path, ~/.janus)
- **providers section**: list of `ProviderConfig` — `id` (str, required), `prefix` (str, required), `api_type` (str, required: openai_compat/anthropic/gemini/opencode_free), `base_url` (str, required), `api_key` (str|None), `models` (list[str])
- **combos section**: list of `ComboConfig` — `name` (str, required), `models` (list[str], required, format: `prefix/model`)
- **api_keys section**: list[str] of static keys (in addition to DB-managed keys)
- **token_savers section**: `rtk` (enabled: true), `caveman` (enabled: false), `ponytail` (enabled: false, level: full/lite/ultra)
- **pricing section**: dict of model name → {input_per_mtok, output_per_mtok, cache_creation_per_mtok, cache_read_per_mtok}
- Full example YAML at the end showing all sections

- [ ] **Step 2: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: write configuration reference"
```

---

### Task 10: docs/providers.md

**Files:**
- Create: `docs/providers.md`

- [ ] **Step 1: Write the provider setup guide**

Create `docs/providers.md`. Cover:

- Explanation of the provider model: `api_type`, `prefix`, `base_url`, `models`
- Provider types table (openai_compat, anthropic, gemini, opencode_free)
- Per-provider setup sections with YAML examples for: OpenAI, Anthropic, Google Gemini, Groq, Together AI, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI (Grok), Qwen/DashScope, OpenCode Zen
- Multi-account setup (same prefix, different id/api_key)
- Known base URLs table (same as README)

Each provider section should include:
```yaml
providers:
  - id: <provider-name>
    prefix: <prefix>
    api_type: <type>
    base_url: <url>
    api_key: ${<ENV_VAR>}
    models: [<model-list>]
```

- [ ] **Step 2: Commit**

```bash
git add docs/providers.md
git commit -m "docs: write provider setup guide"
```

---

### Task 11: docs/api-reference.md

**Files:**
- Create: `docs/api-reference.md`

- [ ] **Step 1: Write the API reference**

Create `docs/api-reference.md` with hand-written docs for all 4 endpoints. Use the route details from the codebase exploration. Each endpoint should have:

**POST /v1/chat/completions** — OpenAI Chat Completions format. Request body example with model (prefix/model or combo name), messages, stream, max_tokens, temperature, tools. Response example (both non-streaming and streaming SSE format). Note: streaming returns `text/event-stream`.

**POST /v1/messages** — Anthropic Messages format. Request body example. Response example (both formats). Streaming SSE format.

**GET /v1/models** — Returns `{"object": "list", "data": [...]}`. Each model: `{"id": "prefix/model", "object": "model", "created": 0, "owned_by": "config-id"}`. Combos: `{"id": "combo-name", "object": "model", "created": 0, "owned_by": "combo"}`.

**GET /v1/health** — No auth required. Returns `{"status": "ok"}`.

Include:
- Authentication section (Authorization: Bearer header, when required)
- Error responses (401 Invalid API key, 429 Budget exceeded + Retry-After, 503 All providers exhausted)
- Streaming format note (SSE with `data:` lines, `data: [DONE]` terminator for OpenAI, event-type-prefixed for Anthropic)
- curl examples for each endpoint

- [ ] **Step 2: Commit**

```bash
git add docs/api-reference.md
git commit -m "docs: write API reference"
```

---

### Task 12: docs/combos.md

**Files:**
- Create: `docs/combos.md`

- [ ] **Step 1: Write the combos guide**

Create `docs/combos.md`. Cover:

- What combos are: named ordered model sequences
- YAML config format: `name`, `models` (list of `prefix/model` strings)
- Example config with a "best-effort" combo
- How clients use them: send combo name as `model` field
- Fallback behavior: tries each model in order with all available accounts, skipping cooled-down accounts
- Cooldown durations table: 429→60s, 5xx→30s, auth→300s, network→15s
- Multi-account behavior: each model in the combo expands to all accounts with that prefix
- Streaming note: no mid-stream retry (can't replay partial output)
- Error classification: which errors are fallback-eligible (429, 401, 403, ≥500, timeout, connection error)

- [ ] **Step 2: Commit**

```bash
git add docs/combos.md
git commit -m "docs: write combos and fallback guide"
```

---

### Task 13: docs/token-savers.md, docs/budgets.md, docs/dashboard.md

**Files:**
- Create: `docs/token-savers.md`
- Create: `docs/budgets.md`
- Create: `docs/dashboard.md`

- [ ] **Step 1: Write token-savers.md**

Cover all three savers:

- **RTK** (default ON): compresses `tool_result` content parts. Auto-detects format (git diff, file listing, log), strips ANSI/diff-mode/permissions, deduplicates lines, smart-truncates at 8000 chars. Config: `token_savers.rtk.enabled` (default true).
- **Caveman**: prepends brevity-maximizing system prompt. Config: `token_savers.caveman.enabled` (default false).
- **Ponytail**: prepends lazy-dev system prompt. Three levels: lite, full, ultra. Config: `token_savers.ponytail.enabled` (default false), `token_savers.ponytail.level` (default "full").
- Pipeline behavior: savers run in order (RTK → Caveman → Ponytail), fail-safe (exceptions caught and logged).
- YAML example showing all three configured.

- [ ] **Step 2: Write budgets.md**

Cover:

- What budgets are: daily spending limits
- Per-key vs global (key_id NULL = global)
- Warn threshold (default 80%): request proceeds, dashboard shows amber
- Hard threshold (100%): request rejected with 429 + Retry-After header
- Most restrictive wins (both per-key and global checked)
- Fail-safe: DB errors don't block requests
- CLI management: `janus budgets list/set/delete`
- Dashboard management: create/delete budgets at `/dashboard/budgets`
- YAML/CLI config examples
- Budget status values: ok, warning, exceeded

- [ ] **Step 3: Write dashboard.md**

Cover all 7 pages:

- **Overview** (`/dashboard`): key stats, provider count, combos count, today's cost, global budget bar
- **Providers** (`/dashboard/providers`): all registered providers with details
- **Combos** (`/dashboard/combos`): all registered combos with model chains
- **API Keys** (`/dashboard/keys`): key list with create/revoke via HTMX
- **Usage** (`/dashboard/usage`): usage statistics
- **Analytics** (`/dashboard/analytics`): spend trends (Chart.js), breakdown by model/provider/account/key, success rate donut chart. Query params: `days` (default 30), `dimension` (default "model")
- **Budgets** (`/dashboard/budgets`): budget list with live status, create/delete via HTMX

- [ ] **Step 4: Commit**

```bash
git add docs/token-savers.md docs/budgets.md docs/dashboard.md
git commit -m "docs: write token-savers, budgets, and dashboard guides"
```

---

### Task 14: docs/cli.md

**Files:**
- Create: `docs/cli.md`

- [ ] **Step 1: Write the CLI reference**

Create `docs/cli.md`. Document every command and subcommand from the CLI exploration:

- `janus serve` — `--port/-p` (default 20128), `--host` (default 127.0.0.1), `--config/-c` (default ~/.janus/config.yaml), `--reload`
- `janus config-init` — `--path/-p` (default ~/.janus/config.yaml)
- `janus config-path`
- `janus keys create` — `--name/-n` (default "default"), `--config/-c`
- `janus keys list` — `--config/-c`
- `janus keys revoke` — `key_id` (required positional arg), `--config/-c`
- `janus usage stats` — `--config/-c`
- `janus usage cost` — `--days/-d` (default 30), `--config/-c`
- `janus usage by-key` — `--days/-d` (default 30), `--config/-c`
- `janus budgets list` — `--config/-c`
- `janus budgets set` — `--daily/-d` (required float), `--key/-k` (default "global"), `--warn/-w` (default 80), `--config/-c`
- `janus budgets delete` — `budget_id` (required positional arg), `--config/-c`
- `janus pricing list` — `--config/-c`
- `janus pricing show` — `model` (required positional arg), `--config/-c`

Each command should show example usage and sample output.

- [ ] **Step 2: Commit**

```bash
git add docs/cli.md
git commit -m "docs: write CLI reference"
```

---

### Task 15: docs/architecture.md

**Files:**
- Create: `docs/architecture.md`

- [ ] **Step 1: Write the architecture page**

Create `docs/architecture.md`. Cover:

- **Canonical intermediate model**: 2N vs N² explanation, formats/providers never import each other
- **Request flow**: detailed step-by-step (parse_request → CanonicalRequest → SaverPipeline → budget check → FallbackHandler → build_upstream_request → provider call → parse_upstream_response → CanonicalResponse → emit_response → record_usage)
- **Format adapters**: 3 registered (openai, anthropic, gemini), 6 methods each (parse_request, build_upstream_request, parse_upstream_response, emit_response, stream_parser, stream_emitter)
- **Provider executors**: 4 types (openai_compat, anthropic, gemini, opencode_free), Protocol with `call()` and `close()`
- **Routing layer**: ProviderRegistry (list[ProviderConfig] per prefix), FallbackHandler (cooldowns, attempt expansion)
- **Provider lifecycle**: built once in create_app(), cached in app.state.providers, shared httpx.AsyncClient (100 connections, 20 keepalive), closed on shutdown
- **SQLite storage**: api_keys, usage, budgets tables. Schema migration approach (idempotent ALTER TABLE)
- **Pricing**: 28 builtin models, prefix matching, compute_cost at recording time
- **Token savers**: pipeline runs after parsing, before routing, fail-safe
- Mermaid diagram or ASCII flow diagram for the request path

- [ ] **Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: write architecture page"
```

---

### Task 16: README.md updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Replace the `## License` section at the bottom (currently `TBD`):

```markdown
## License

[GPL-3.0](LICENSE) © 2026 Aman Verasia
```

Add documentation and contributing links after the Quickstart section (after line 24, after "Point your coding tool..."):

```markdown
**📚 [Documentation](https://amanverasia.github.io/Janus/) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)**
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README with license, doc links"
```

---

### Task 17: GitHub Actions — publish workflow

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Write the PyPI publish workflow**

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install build tool
        run: pip install build

      - name: Build distributions
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add PyPI publish workflow (OIDC trusted publisher)"
```

---

### Task 18: GitHub Actions — docs deploy workflow

**Files:**
- Create: `.github/workflows/docs.yml`

- [ ] **Step 1: Write the docs deploy workflow**

Create `.github/workflows/docs.yml`:

```yaml
name: Deploy Docs

on:
  push:
    branches: [main]
    paths:
      - "docs/**"
      - "mkdocs.yml"
      - "README.md"

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install MkDocs Material
        run: pip install mkdocs-material

      - name: Build and deploy
        run: mkdocs gh-deploy --force
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci: add GitHub Pages docs deploy workflow"
```

---

### Task 19: Full verification

- [ ] **Step 1: Verify mkdocs builds clean**

Run: `mkdocs build --strict`
Expected: No errors, `site/` directory created

- [ ] **Step 2: Verify Python build works**

Run: `python -m build`
Expected: `dist/janus-0.1.0-py3-none-any.whl` and `dist/janus-0.1.0.tar.gz` created

- [ ] **Step 3: Verify existing tests still pass**

Run: `.venv/bin/python -m pytest`
Expected: All 180 tests pass

- [ ] **Step 4: Verify lint and typecheck**

Run:
```bash
.venv/bin/ruff check src/janus/ tests/
.venv/bin/ruff format --check src/janus/ tests/
.venv/bin/mypy src/janus/
```
Expected: All clean (no source code changed, but verify nothing broke)

- [ ] **Step 5: Clean up build artifacts**

Run: `rm -rf dist/ site/`

- [ ] **Step 6: Final commit if any cleanup needed**

If `.gitignore` changes are needed:
```bash
git add -A
git commit -m "chore: cleanup build artifacts"
```

---

### Task 20: AGENTS.md update

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add docs and packaging section to AGENTS.md**

Add a new section to AGENTS.md covering:

- Docs: MkDocs Material, `mkdocs serve` for local preview, `mkdocs build --strict` to verify
- Docs structure: `docs/` pages + `mkdocs.yml` config, `docs/superpowers/` excluded from site
- Packaging: hatchling build backend, `python -m build` for wheel+sdist
- CI: `publish.yml` (PyPI OIDC on tag), `docs.yml` (gh-pages on main)
- Manual prerequisites: PyPI trusted publisher config, GitHub Pages gh-pages branch
- Dev deps: `mkdocs-material`, `build` in `[dev]` extras

Add to the Dev environment / Commands sections as appropriate.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md for Phase 8 (docs & packaging)"
```

---

### Task 21: PR

- [ ] **Step 1: Create branch and push**

```bash
git checkout -b phase-8/docs-packaging
git push -u origin phase-8/docs-packaging
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --title "Phase 8: Documentation & Packaging" --body "Closes Phase 8 design spec.

## Changes
- GPLv3 LICENSE
- pyproject.toml metadata (classifiers, URLs, license)
- CHANGELOG.md (v0.1.0)
- CONTRIBUTING.md
- MkDocs Material docs site (12 pages)
- GitHub Actions: PyPI publish (OIDC) + docs deploy (gh-pages)
- README polish (license, doc links)
- .gitignore (site/, dist/, *.egg-info/)
- AGENTS.md updated

## Verification
- [x] mkdocs build --strict passes
- [x] python -m build produces valid wheel+sdist
- [x] All 180 tests pass
- [x] ruff + mypy clean" --base main
```

- [ ] **Step 3: After squash-merge, pull and prune**

```bash
git checkout main
git pull --rebase origin main
git branch -d phase-8/docs-packaging
git push origin --delete phase-8/docs-packaging
```
