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
mkdir -p janus-data
janus config-init --path janus-data/config.yaml
docker compose up -d
```

See [Deployment](deployment.md) for volume layout, env vars, and remote access.

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

This creates `~/.janus/config.yaml`. It only needs to exist: the easiest way to
add credentials is the dashboard's **Connect** screen (see
[Open the dashboard](#open-the-dashboard) below). For file-based or headless
setup, add providers to the YAML instead:

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

!!! important "YAML is a seed file"
    On first startup, Janus imports `providers`, `combos`, `token_savers`, and
    `pricing` from YAML into SQLite. After that, the **database is the source of
    truth**. Editing YAML and restarting will **not** re-apply changes. Use the
    [dashboard](dashboard.md) or **Export Config** / **Reset to Defaults** on the
  Settings page. See [Configuration — DB-driven config](configuration.md#db-driven-configuration).

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

The root URL `/` redirects to the Cloudline dashboard at `/dashboard/ui`.

## Open the dashboard

The dashboard always requires a Janus API key, including on localhost. Create
your first one (the full `sk-janus-...` value is shown once):

```bash
janus keys create --name admin
```

Then run `janus dashboard` (or visit `http://localhost:20128/dashboard/ui`) and
sign in with that key.

1. **Home** shows a three-step checklist while setup is incomplete: connect
   credentials, create a client key, and send a first request.
2. **Connect** takes pasted API keys or dropped credential files, previews them
   with masked values, and imports the new ones with **Make these routable** on.
   See [Key Inventory — Connect](inventory.md#connect-paste-or-drop-credentials).
3. The **Your endpoint** card on Home shows your base URL and a copyable `curl`
   snippet. **Settings → Tools** has per-client setup for Claude Code, Codex,
   Cursor, and Cline.

See [Dashboard](dashboard.md) for every section and tab.

## Your First Request

Send an OpenAI-format request to Janus. If **Require API key** is on, add
`-H "Authorization: Bearer sk-janus-yourkey"`:

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

Gemini-native tools can use:

```bash
curl "http://localhost:20128/v1beta/models/openai/gpt-4o:generateContent" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello!"}]}]}'
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
- [Key Inventory](inventory.md) — manage many upstream API keys
- [Dashboard](dashboard.md) — explore the web UI at `/dashboard/ui`
