# Phase 8: Documentation & Packaging

## Goal

Package Janus for PyPI publication and build a comprehensive documentation site using MkDocs Material. This makes Janus installable via `pip install janus` and gives users a professional docs site for setup, configuration, and reference.

## Decisions

- **License:** GPLv3 (`GPL-3.0-only`)
- **Docs tool:** MkDocs Material with hand-written API reference (no mkdocstrings)
- **PyPI publishing:** Automated via GitHub Actions OIDC trusted publisher (no API token)
- **Docs hosting:** GitHub Pages at `https://amanverasia.github.io/Janus/`
- **API reference:** Hand-written with curl examples and JSON samples (small, stable API surface — 4 HTTP endpoints)

## Deliverables

### 1. LICENSE

Full GPLv3 text at repo root.

### 2. pyproject.toml changes

Add the following metadata fields:

```toml
license = "GPL-3.0-only"
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: FastAPI",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Internet :: Proxy Servers",
]

[project.urls]
Homepage = "https://github.com/amanverasia/Janus"
Documentation = "https://amanverasia.github.io/Janus/"
Repository = "https://github.com/amanverasia/Janus"
Issues = "https://github.com/amanverasia/Janus/issues"
```

Add to `[project.optional-dependencies] dev`:
- `mkdocs-material>=9.5`
- `build>=1.2`

### 3. CI Workflows

#### `.github/workflows/publish.yml` — PyPI publish

- Triggered on tag push matching `v*` pattern
- Steps: checkout, setup Python 3.11, `pip install build`, `python -m build`, publish via `pypa/gh-action-pypi-publish@release/v1`
- Uses OIDC trusted publisher (no API token in secrets)
- Requires manual repo configuration on PyPI: add GitHub as trusted publisher with environment `pypi`

#### `.github/workflows/docs.yml` — GitHub Pages deploy

- Triggered on push to `main` (only when `docs/`, `mkdocs.yml`, or `README.md` change)
- Steps: checkout, setup Python 3.11, `pip install mkdocs-material`, `mkdocs gh-deploy --force`
- Builds site and pushes to `gh-pages` branch
- GitHub Pages must be configured to serve from `gh-pages` branch (manual one-time setup)

### 4. mkdocs.yml

At repo root. Configuration:

- **Theme:** Material with dark/light toggle, palette toggle
- **Plugins:** search
- **Nav structure:** 4 sections
  - Getting Started: index, getting-started, client-setup
  - Guides: providers, combos, token-savers, budgets, dashboard
  - Reference: configuration, api-reference, cli
  - Development: architecture, contributing (link to CONTRIBUTING.md)
- **Markdown extensions:** admonition, pymdownx.superfences (for code blocks), pymdownx.tabbed, toc with permalink
- `docs/superpowers/` excluded from nav (internal design docs)

### 5. Documentation Pages (`docs/`)

All pages hand-written in Markdown. Existing `docs/superpowers/` remains untouched.

| File | Content |
|---|---|
| `docs/index.md` | Project landing page — what Janus is, key features, quick install, link to getting-started |
| `docs/getting-started.md` | Install (pip + Docker), config-init, first request walkthrough, verification |
| `docs/configuration.md` | Full YAML reference: `server`, `providers`, `combos`, `token_savers`, `pricing`, `api_keys` sections with all fields documented |
| `docs/providers.md` | Provider setup by provider: OpenAI, Anthropic, Gemini, Groq, Together, DeepSeek, OpenRouter, Mistral, Fireworks, Perplexity, xAI, Qwen, OpenCode Zen. Each with base_url, api_type, model list, notes. |
| `docs/client-setup.md` | How to point each tool at Janus: Claude Code (ANTHROPIC_BASE_URL), Codex/Cursor (OPENAI_BASE_URL), Cline, generic OpenAI-compatible clients |
| `docs/api-reference.md` | Hand-written HTTP API docs for 4 endpoints: `POST /v1/chat/completions`, `POST /v1/messages`, `GET /v1/models`, `GET /v1/health`. Each with curl examples, request/response JSON samples, parameters table. |
| `docs/combos.md` | Combo concept, YAML config, ordering semantics, fallback behavior, multi-account rotation |
| `docs/budgets.md` | Daily budget setup, per-key vs global, warn/block thresholds, CLI management, dashboard integration |
| `docs/dashboard.md` | Dashboard pages walkthrough: Overview, Providers, Combos, API Keys, Usage, Analytics, Budgets. Screenshots not required (describe layout). |
| `docs/token-savers.md` | RTK (default ON, compression behavior), Caveman (terse prompt), Ponytail (lazy-dev, 3 levels). Config examples. |
| `docs/cli.md` | Full CLI reference: serve, config-init, config-path, keys (create/list/revoke), usage (stats/cost/by-key), budgets (list/set/delete), pricing (list/show). All flags documented. |
| `docs/architecture.md` | Canonical intermediate model, request flow diagram (text/mermaid), provider lifecycle, connection pooling, fallback layer, storage layer |

### 6. CONTRIBUTING.md

At repo root. Sections:

- **Development setup** — clone, `.venv`, `pip install -e ".[dev]"`
- **Running tests / lint / typecheck** — exact commands from AGENTS.md
- **Architecture constraint** — canonical model boundary (formats/providers never import each other)
- **Adding a new format adapter** — 6-method summary, FORMATS dict registration
- **Adding a new provider executor** — Protocol requirements, _build_provider, _resolve_format
- **Adding a new token saver** — TokenSaver protocol, pipeline construction
- **PR process** — squash-merge, branch naming, test requirements
- **Docs preview** — `pip install -e ".[dev]"` then `mkdocs serve`

### 7. CHANGELOG.md

Keep-a-changelog format. Initial entry:

```markdown
# Changelog

## [0.1.0] - 2026-06-25

### Added
- Core routing gateway with canonical intermediate model
- 3 format adapters: OpenAI, Anthropic, Gemini
- 4 provider executors: openai_compat, anthropic, gemini, opencode_free
- SSE streaming translation
- Multi-account fallback routing with cooldowns (429/5xx/auth/network)
- Named combos (ordered model sequences)
- Token savers: RTK compression (default ON), Caveman, Ponytail
- SQLite persistence: API keys, usage tracking, budgets
- Pricing engine (28 builtin models, YAML-overridable, prefix matching)
- Budget enforcement (per-key + global, warn at 80%, block at 100%)
- Analytics: cost tracking, spend trends, success rates, breakdowns
- HTMX dashboard (Overview, Providers, Combos, Keys, Usage, Analytics, Budgets)
- CLI: serve, config-init, keys, usage, budgets, pricing
- Docker support (multi-stage build, docker-compose with volume persistence)
- GitHub Actions CI (ruff, mypy, pytest)
```

### 8. README.md updates

- Replace `TBD` license with GPLv3 reference and badge
- Add documentation site link (`https://amanverasia.github.io/Janus/`)
- Add contributing link
- Add MkDocs Material badge or similar
- Keep concise — README is a landing page, full docs live on the docs site

### 9. .gitignore additions

```
site/          # MkDocs build output
dist/          # Python build artifacts
*.egg-info     # Editable install metadata
```

## Manual Prerequisites

These are one-time manual steps that cannot be automated in code:

1. **PyPI trusted publisher:** On PyPI, add GitHub as a trusted publisher for the `janus` project (or register it if the name is taken — may need a different name). Configure with environment `pypi`, workflow file `publish.yml`.
2. **GitHub Pages:** In repo Settings > Pages, set source to `gh-pages` branch.
3. **First release:** Create and push `v0.1.0` tag to trigger the publish workflow.

## Testing

- No new unit tests — this is docs/packaging only
- CI verification: `mkdocs build` succeeds (strict mode)
- CI verification: `python -m build` produces valid wheel + sdist
- Existing 180 tests must still pass

## Out of Scope

- Source code docstrings (future improvement)
- Versioning automation (e.g., bumpversion, setuptools-scm) — manual tag for now
- Azure OpenAI / AWS Bedrock providers
- Streaming usage recording
