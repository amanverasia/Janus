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
