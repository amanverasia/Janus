# Getting Started

## Installation

### pip

```bash
pip install janus-ai
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
