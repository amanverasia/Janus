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
TLS-terminating reverse proxy must set `X-Forwarded-Proto` and be explicitly trusted
by Uvicorn as described below. Username/password
login and the loopback bypass are not supported. Legacy dashboard credential settings are
purged during database initialization. See
[Dashboard — Authentication](dashboard.md#authentication).

The `require_api_key` setting controls API endpoint authentication only. It does
not make the dashboard anonymous when disabled.

## Reverse proxy

Janus does not terminate TLS itself. Its dashboard accepts a browser mutation only
when the browser's `Origin` matches the request's scheme, host, and port. Both the
origin check and login cookie's `Secure` flag use the same resolved request scheme.
A proxy must supply the external scheme and preserve the external `Host`, including
any non-default port. A missing HTTPS scheme causes a legitimate mutation to return
403; weakening the origin comparison is not a deployment fix.

### Trust boundary

`janus serve` uses Uvicorn's proxy-header middleware. Set `FORWARDED_ALLOW_IPS` to
the connecting proxy's IP address, or a dedicated network containing only trusted
proxies. It defaults to `127.0.0.1`, and Docker Compose forwards the setting from
`.env`. For a proxy in another container, loopback is not that proxy's address.
Do not use `*` or a shared network containing untrusted containers. Restrict direct
access to Janus with loopback binding, a private container network, or a firewall.

Uvicorn accepts `X-Forwarded-Proto` and `X-Forwarded-For` only from trusted peers;
it does not use `X-Forwarded-Host` or the standard `Forwarded` header to replace
`Host`. The trusted proxy must overwrite incoming forwarding headers and accept
only its configured public host. Client-supplied forwarding values must never
become authoritative. See [Uvicorn settings](https://www.uvicorn.org/settings/#http)
and [proxy deployment guidance](https://www.uvicorn.org/deployment/#running-behind-nginx).

### nginx on the same host

Terminate TLS at nginx and forward to a loopback-only Janus listener. Within the
nginx HTTPS server for `gateway.example` (with your certificate configured):

```nginx
server_name gateway.example;

location / {
    proxy_pass http://127.0.0.1:20128;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Forwarded "";
    proxy_set_header X-Forwarded-Host "";
    proxy_set_header X-Forwarded-Port "";
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

Reject unknown hosts in a separate default server and redirect public HTTP to HTTPS.
For native `janus serve`, bind `--host 127.0.0.1` and set
`FORWARDED_ALLOW_IPS=127.0.0.1`. For a host proxy connecting to a published Docker
port, Docker may translate the connecting peer address: inspect that address and
trust only the controlled proxy path rather than broadening trust to all clients.

### nginx in Docker

For a proxy container on a dedicated bridge, replace `proxy_pass` above with
`http://janus:20128`. Publish only the proxy's TLS port. This standalone Compose
example uses fixed addresses to make the trusted peer explicit; choose an unused
subnet and configure the TLS server, certificates, and host rejection in `nginx.conf`:

```yaml
services:
  janus:
    image: ghcr.io/amanverasia/janus:<pinned-version>
    volumes:
      - ./janus-data:/home/janus/.janus
    environment:
      FORWARDED_ALLOW_IPS: 172.30.40.2
      INVENTORY_ENCRYPTION_KEY: ${INVENTORY_ENCRYPTION_KEY:?required}
    networks:
      gateway:
        ipv4_address: 172.30.40.3
    restart: unless-stopped
  proxy:
    image: nginx:stable-alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
    networks:
      gateway:
        ipv4_address: 172.30.40.2
    restart: unless-stopped
networks:
  gateway:
    ipam:
      config:
        - subnet: 172.30.40.0/24
```

If using the repository's source-build Compose file instead, set the same environment
variable and remove its `20128` port publication in your deployment configuration.
Confirm the final configuration with `docker compose config` locally; it can contain
secrets and must not be pasted into logs or bug reports.

Caddy's `reverse_proxy janus:20128` and Traefik's HTTP TLS router can also supply
scheme metadata. Preserve the original Host (Traefik `passHostHeader=true`), trust
only their backend peer addresses, and keep their own incoming forwarded-header
trust limited to any actual upstream proxy. A raw TLS-to-TCP tunnel does not
necessarily add HTTP headers: place a trusted HTTP proxy after it. Hardcode
`X-Forwarded-Proto https` only on a private listener reachable exclusively through
that TLS tunnel; a public HTTP listener must not assert HTTPS.

### Verify before admitting traffic

1. Visit the public HTTPS dashboard, sign in, and check that the cookie has
   `Secure`, `HttpOnly`, `SameSite=Lax`, and `Path=/dashboard`.
2. In Settings, change a reversible setting, confirm it persists, and restore it.
   The browser's `Origin` must include the public port when it is non-default.
3. Confirm the endpoint shown under Tools uses the public HTTPS host and port.
4. Verify that mutations with another origin, `Origin: null`, malformed origins,
   or a same-host HTTP origin return 403. A direct untrusted client sending
   `X-Forwarded-Proto: https` must not override the request scheme.

No separate external-origin override is needed. Keep strict origin validation and
fix the proxy/peer trust configuration when these checks fail.

## Data backup

For a cold backup, stop Janus before archiving the entire data directory, then
restart it. Keep the archive and the encryption key private:

```bash
docker compose stop janus
(umask 077; tar czf janus-backup.tar.gz janus-data/)
docker compose start janus
```

For an online pre-deployment backup, use SQLite's backup API rather than copying
an active database or its WAL independently:

```bash
docker compose exec -T --user janus janus python - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

root = Path.home() / ".janus"
name = "predeploy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".db"
source = sqlite3.connect(f"file:{root / 'janus.db'}?mode=ro", uri=True)
source.execute("PRAGMA query_only=ON")
target = sqlite3.connect(root / name)
source.backup(target)
assert target.execute("PRAGMA quick_check").fetchone()[0] == "ok"
target.close()
source.close()
(root / name).chmod(0o600)
print(name)
PY
```

Copy the backup off-host, and retain the deployment's config, Compose overrides,
proxy/tunnel configuration, and encryption key separately with restricted access.

The SQLite database is the source of truth after first startup. Restoring
`janus.db` restores providers, combos, pricing overrides, usage history, budgets,
inventory keys, and cooldown state.

## Revision tracking and rollback

Before updating, record the source revision, dirty files, current image ID, effective
proxy trust, and active DB settings privately. Preserve local Compose/proxy changes;
never reset a deployment checkout or blindly overwrite its data directory. Tag the
current image for rollback and create a verified database backup as above.

Source builds can embed the revision in an OCI image label:

```bash
docker compose build --build-arg VCS_REF="$(git rev-parse HEAD)" janus
docker image inspect janus-janus --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose up -d --no-deps janus
```

The local image name depends on your Compose project. Published CI images also
carry the source revision. Record the deployed image ID and revision after recreation;
health alone is insufficient. Repeat the authenticated HTTPS checks above. If a
proxy caches the Janus container IP, reload its upstream resolution after recreation.

For an application rollback, override the `janus` image with the saved rollback
tag and recreate just that service using `--no-build --no-deps`; preserve proxy
configuration and volumes. If a schema rollback is necessary, stop Janus and restore
the matching database backup together with its original image/configuration. Do not
restore an older database over a running process; restoring it discards writes made
since the backup. Retain the old image and backup until the new release is verified.

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
