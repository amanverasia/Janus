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

The image runs the app as user `janus` (uid **1000**). On first `docker compose up`,
Docker may create `./janus-data` on the host as **root**, which blocks SQLite from
opening `janus.db`. The container entrypoint fixes ownership of the mounted data
directory on each start (no manual `chown` needed).

If you still hit permission errors (e.g. host uid is not 1000), fix ownership once:

```bash
sudo chown -R 1000:1000 janus-data
```

Or, without sudo, using a throwaway root container:

```bash
docker run --rm -u 0 -v "$(pwd)/janus-data:/data" alpine sh -c 'chown -R 1000:1000 /data'
```

The compose file passes an explicit list of variables from your host `.env` into the
container: the provider API keys, the OAuth client ids/secrets, and
`INVENTORY_ENCRYPTION_KEY`, `INVENTORY_ENCRYPTION_PREVIOUS_KEY`, `INVENTORY_PUSH_TOKEN`
and `JANUS_ALLOW_INSECURE_DEV_KEY`. A variable that is only in `.env` but not listed under
`environment:` never reaches Janus — add it there (or in a `docker-compose.override.yml`):

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
INVENTORY_ENCRYPTION_KEY=...  # encrypts inventory and gateway provider credentials
INVENTORY_PUSH_TOKEN=...
```

Retain `INVENTORY_ENCRYPTION_KEY` with your backup material. Janus fails clearly if
an encrypted credential is present but the matching key is unavailable; when rotating
the key, set `INVENTORY_ENCRYPTION_PREVIOUS_KEY` to the old key and restart so stored
credentials are re-sealed automatically (see the inventory docs). If no encryption key
is configured while real credentials exist, startup refuses to continue unless
`JANUS_ALLOW_INSECURE_DEV_KEY=1` opts into plaintext storage. Dashboard
configuration export is plaintext by design; a raw database backup remains encrypted.

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

Every client, including `127.0.0.1` and `localhost`, is redirected to
`/dashboard/login` until it authenticates with a valid Janus API key. DB-managed
keys must be active and have **Allow dashboard login** (`can_login=true`);
configured static keys are also accepted. The session cookie is scoped to
`/dashboard` and marked `Secure` when the request arrives over HTTPS, so a
TLS-terminating reverse proxy should forward `X-Forwarded-Proto`. Username/password
login and the loopback bypass are not supported. Legacy dashboard credential settings are
purged during database initialization. See
[Dashboard — Authentication](dashboard.md#authentication).

The `require_api_key` setting controls API endpoint authentication only. It does
not make the dashboard anonymous when disabled.

## Reverse proxy

Janus does not terminate TLS itself. Put nginx, Caddy, or Traefik in front:

```nginx
location / {
    proxy_pass http://127.0.0.1:20128;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
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

The root URL `/` redirects to the Cloudline dashboard at `/dashboard/ui`.

Inventory submissions, previews, push requests, and bulk actions accept bodies up to 4 MiB;
backup imports accept up to 16 MiB including multipart overhead. Larger requests return 413,
including requests without Content-Length. All credential checks share the
`CHECK_CONCURRENCY` limit (default 8), including scheduled and bulk checks.

Inventory submission rate limits use the resolved client address. Behind a reverse proxy,
forward `X-Forwarded-For` and configure Uvicorn's `FORWARDED_ALLOW_IPS` to trust only your
proxy addresses (loopback is trusted by default). Untrusted forwarded headers do not change
the rate-limit bucket.
