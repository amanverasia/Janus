# Deployment

Janus is designed as a local-first, single-user gateway. This page covers Docker
deployment and production-oriented configuration.

## Docker

### Pull pre-built image

Multi-arch images (amd64 + arm64) are published to GitHub Container Registry:

```bash
docker pull ghcr.io/amanverasia/janus:latest
```

### docker compose (recommended)

```bash
mkdir -p janus-data
janus config-init --path janus-data/config.yaml
# Edit janus-data/config.yaml — add providers, API keys via ${ENV_VAR}

docker compose up -d
```

The compose file mounts `./janus-data` to `/home/janus/.janus` inside the
container. This persists:

- `config.yaml` — seed config (loaded once on first startup)
- `janus.db` — SQLite database (providers, combos, usage, inventory, etc.)

Environment variables from your host `.env` file are passed through for
`${ENV_VAR}` resolution in config:

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
INVENTORY_ENCRYPTION_KEY=...
INVENTORY_PUSH_TOKEN=...
```

### Build from source

```bash
git clone https://github.com/amanverasia/Janus.git
cd Janus
docker compose up -d --build
```

### Default bind address

The Docker image binds to `0.0.0.0:20128` (all interfaces). **Enable API key
auth** when exposing Janus beyond localhost:

```yaml
server:
  host: 0.0.0.0
  require_api_key: true
```

Or toggle `require_api_key` at runtime from the dashboard Settings page.

## Remote access

### API endpoints

When `host: 0.0.0.0`, clients connect to `http://<host>:20128/v1/...`. Require
API keys and use TLS termination (reverse proxy) for anything beyond a trusted LAN.

### Dashboard authentication

Loopback clients (`127.0.0.1`, `localhost`) access the dashboard without auth.
Remote clients are redirected to `/dashboard/login` and must authenticate with a
valid Janus API key (sets an httponly cookie). See
[Dashboard — Authentication](dashboard.md#authentication).

## Reverse proxy

Janus does not terminate TLS itself. Put nginx, Caddy, or Traefik in front:

```nginx
location / {
    proxy_pass http://127.0.0.1:20128;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;   # important for SSE streaming
}
```

For streaming (`stream: true`), disable proxy buffering.

## Data backup

Back up the entire data directory:

```bash
tar czf janus-backup.tar.gz janus-data/
```

The SQLite database is the source of truth after first startup. Restoring
`janus.db` restores providers, combos, pricing overrides, usage history, budgets,
inventory keys, and cooldown state.

## Health check

```bash
curl http://localhost:20128/v1/health
# {"status": "ok"}
```

The root URL `/` redirects to `/dashboard`.
