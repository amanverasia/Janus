# Key Inventory

Janus includes an **upstream key inventory** — a separate subsystem for storing,
validating, and routing with many API keys across 27+ providers. Keys are checked
for validity, credit balance (where supported), and model access, then wired into
gateway routing as multi-account pools.

Add credentials on the dashboard's **Connect** screen (`/dashboard/ui/connect`) and
manage them under **Inventory** (`/dashboard/ui/inventory`). For headless setups,
use the [Push API](#push-api) or the CLI (`janus inventory`).

## How it works

1. **Add keys** — paste or drop credentials on **Connect**, restore a JSON export,
   or push via API. Connect previews every entry before anything is stored.
2. **Auto-detect provider** — Janus probes each key and assigns a provider (or
   marks it `unidentified`).
3. **Validate & recheck** — keys are checked on ingest and on a background schedule
   (default: every 12 hours).
4. **Route requests** — when a gateway provider prefix has routable inventory keys,
   Janus expands it into one account per key (same fallback/cooldown behavior as
   multi-account YAML config).

### Inventory ↔ gateway bridge

Gateway providers use a `prefix` (e.g. `openai`, `gemini`). Inventory keys are
stored per inventory provider ID. Most prefixes map 1:1; the exception:

| Gateway prefix | Inventory provider ID |
|---|---|
| `gemini` | `google` |

If routable inventory keys exist for a prefix, they **replace** the gateway
provider's static `api_key` and expand into multiple accounts. If no routable keys
exist, the gateway provider's configured `api_key` is used as before.

## Dashboard pages

### Overview — `/dashboard/ui/inventory`

Credit summary, provider cards, best keys, recent activity, and encryption status.

### Keys — `/dashboard/ui/inventory/keys`

Filter by provider or status, search, sort, and paginate. Per-key actions:

- **Recheck** — re-validate a single key
- **Delete** — remove from inventory
- **Reclassify** — fix misidentified provider assignments (bulk action on overview)

### Connect: paste or drop credentials

**Connect → Keys and logins** (`/dashboard/ui/connect`) replaces the former Add keys
screen; `/dashboard/ui/inventory/add` redirects there.

1. Paste keys (one per line) or a credential JSON export, or drop files onto the
   page. Files are read in the browser with no upload step.
2. Leave the provider on **Auto**, or pick a provider. Auto recognizes the
   credential files listed under [Supported credential formats](#supported-credential-formats);
   pick the provider yourself for bare OAuth access tokens. An optional custom
   base URL is under **Advanced**.
3. **Preview** classifies every entry through the [Preview API](#preview-api):
   per-provider counts plus a masked table where each entry is `new`, `exists`,
   or `rejected`. Nothing is written to the database.
4. **Import** is enabled when at least one entry is `new`. It submits through
   `POST /dashboard/api/inventory/submit` with **Make these routable**
   (`provision_routing=true`) on by default, which creates the matching gateway
   routing provider when one is missing. Imported keys are validated in the
   background; the page refreshes until validation settles.

### Restore backup — `/dashboard/ui/connect/restore`

Import a **Dashboard_For_Apis** JSON export. Use this when migrating from another
Janus node or key-management tool. `/dashboard/ui/inventory/import` redirects
here.

## Supported credential formats

| Input | Provider selection | Notes |
|---|---|---|
| Raw provider API keys, one per line | **Auto** | Keys with a distinctive prefix (`gsk_`, `nvapi-`, `sk-ant-`, `sk-proj-`, `sk-or-v1-`, `xai-`, …) are assigned by prefix, and preview and import always agree. Generic `sk-…` and prefix-less keys show as *Detected on import* in the preview; import identifies them by probing providers. Keys that no provider accepts are stored as `unidentified` for review. |
| Codex CLI `~/.codex/auth.json` | **Auto** | The nested `tokens` object (access, refresh, id token, account id) is flattened into a Codex credential. |
| Codex (ChatGPT) OAuth JSON | **Auto** or **Codex (ChatGPT)** | See [Codex / ChatGPT OAuth](#codex-chatgpt-oauth). A 9router `providerConnections` export with several accounts becomes one inventory entry per Codex connection. |
| Cline account | **Auto** or **Cline** | A WorkOS access token with the `workos:` prefix, a JSON object with `"provider": "cline"` plus `accessToken` / `refreshToken`, or Cline entries in a `providerConnections` export. The refresh token is kept so Janus can renew the ~1 hour access token. Pick **Cline** for a token without the `workos:` prefix. |
| Antigravity (Google) OAuth JSON | **Auto** or **Antigravity (Google)** | Needs an access token (`access_token` or `accessToken`); refresh token, expiry, and `projectId` are kept when present. Auto needs `projectId`; pick the provider for a bare access token. |
| Kiro (AWS) OAuth JSON | **Auto** or **Kiro (AWS)** | A credential blob containing `accessToken` and `refreshToken`. Auto needs `profileArn` or `authMethod`. |

Claude Code `~/.claude/.credentials.json` is recognized but not stored in the
inventory: the preview rejects it with a pointer to **Routing → Providers**, where
it can be added as a Claude OAuth provider (see
[Subscription / OAuth providers](client-setup.md#subscription-oauth-providers)).
Other credential files are not supported and come back from the preview as
`rejected` with an "unsupported credential format" message. Gemini CLI
`oauth_creds.json` is one example.

## Supported inventory providers

Janus recognizes keys for these providers (auto-detection probes each):

OpenAI, Anthropic, OpenRouter, Google AI (Gemini), Ollama Cloud, Groq, Together, Perplexity,
Cohere, Mistral, DeepSeek, xAI, Hugging Face, Replicate, Fireworks, NVIDIA,
Moonshot, DashScope (Qwen), MiniMax, SiliconFlow, StepFun, Zhipu, Xiaomi, Tavily,
Firecrawl, fal.ai, Exa, Brave Search, **Codex (ChatGPT)**, **Cline**, **Antigravity**,
and **Kiro** (OAuth credentials), plus **custom** and **unidentified** fallbacks.

### Codex / ChatGPT OAuth

Paste or drop one of the following on **Connect**, then choose provider **Codex (ChatGPT)**:

- A Janus credential JSON blob (`access_token` / `refresh_token` / optional
  `extra.workspaceId`)
- A 9router Codex `providerConnections` row (camelCase tokens +
  `providerSpecificData.chatgptAccountId`)
- A JSON array of those rows, or a wrapper with a `providerConnections` array
  (non-Codex connections are skipped)
- A bare access token (short-lived; prefer blobs that include a refresh token)

Full 9router backup import (settings, combos, other providers) is not supported
here — only connection objects. Pasting a Codex blob on the **Providers** page
still works for a single gateway account.

## Encryption at rest

Set `INVENTORY_ENCRYPTION_KEY` to a Fernet key before adding credentials. Inventory
upstream keys and gateway provider API keys/OAuth credential blobs are then stored
encrypted in SQLite. Keep this key with your backups: an encrypted database cannot
be used without the same Fernet key.

Generate a key:

```bash
janus inventory generate-encryption-key
# gAAAAABl...  (save this — shown once)
```

```bash
export INVENTORY_ENCRYPTION_KEY='gAAAAABl...'
```

Encrypt existing plaintext upstream keys and provider credentials in one pass:

```bash
janus inventory encrypt-keys
```

The dashboard shows separate encryption counts on the inventory overview and offers
one **Encrypt credentials** action when `INVENTORY_ENCRYPTION_KEY` is set. Dashboard
configuration export remains a portable plaintext YAML export; protect exported files
accordingly. For encrypted backups, copy the SQLite database and retain the Fernet key.

### Startup hardening and key rotation

On startup Janus audits stored credentials:

- When `INVENTORY_ENCRYPTION_KEY` is set, any still-plaintext credential is sealed with
  the current key, and rows previously encrypted with another key are re-sealed if
  `INVENTORY_ENCRYPTION_PREVIOUS_KEY` holds the old key. Re-sealing uses a
  compare-and-swap on the stored ciphertext, so concurrent writers cannot lose data.
- Credentials that can't be decrypted with the current (or previous) key are logged at
  `ERROR` with their row ids, reported as a critical dashboard banner, flagged with
  `decryptable: false` on the provider and inventory-keys state sections, and skipped
  by routing — they never cause a 500.
- If `INVENTORY_ENCRYPTION_KEY` is unset while real credentials are stored, Janus
  refuses to start rather than keeping them in plaintext. Set the key, or opt into
  insecure plaintext storage explicitly with `JANUS_ALLOW_INSECURE_DEV_KEY=1` (a
  prominent warning is logged). Rotation never re-seals onto this insecure fallback.

Rotating the encryption key:

```bash
janus inventory generate-encryption-key   # new key
export INVENTORY_ENCRYPTION_PREVIOUS_KEY="$INVENTORY_ENCRYPTION_KEY"
export INVENTORY_ENCRYPTION_KEY='gAAAAABl...'  # the new key
# restart Janus; stored credentials are re-sealed onto the new key
```

After a successful restart with every credential re-sealed, unset
`INVENTORY_ENCRYPTION_PREVIOUS_KEY`.

## Preview API

`POST /dashboard/api/inventory/preview` is the read-only half of
`/dashboard/api/inventory/submit`. **Connect** uses it, and scripts can call it
with dashboard authentication to check a paste before importing it.

- **Input:** the same form fields as `submit` — `keys_text` plus an optional
  `provider_id` (default `auto`). Input is parsed the same way, so a Codex
  `providerConnections` export expands to one entry per connection.
- **Output:** per-provider summary counts and one row per entry with the provider,
  masked key, label, and a status of `new`, `exists`, or `rejected` (with an
  error message). Raw keys and token values are never returned, and responses
  are sent with `Cache-Control: no-store`.
- **No writes:** previewing never creates, updates, or reclassifies inventory
  rows.
- **Limits:** the submit batch cap (`INVENTORY_MAX_SUBMIT_BATCH`) and rate limiter
  apply, and the request body is capped at 1 MiB. Empty or oversized input returns
  `422`; a rate-limited request returns `429`.

## Push API

Programmatically ingest keys from scripts or other nodes:

```bash
curl -X POST http://localhost:20128/dashboard/api/inventory/push \
  -H "Authorization: Bearer $INVENTORY_PUSH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "keys": [
      {"key": "sk-...", "label": "prod-1", "provider": "openai"},
      {"key": "sk-ant-...", "provider": "anthropic"}
    ],
    "node_id": "my-laptop"
  }'
```

Single-key shorthand:

```json
{"key": "sk-...", "label": "backup", "provider": "openai"}
```

Set `INVENTORY_PUSH_TOKEN` in the environment. Requests without a valid token
receive `401`.

Rate limits apply (default: 300 keys per minute per client IP). Batch size is
capped at 200 keys per request.

## CLI

### `janus inventory generate-encryption-key`

Print a Fernet key suitable for `INVENTORY_ENCRYPTION_KEY`.

### `janus inventory encrypt-keys`

Encrypt all plaintext upstream keys in the database. Requires
`INVENTORY_ENCRYPTION_KEY` to be set.

### `janus inventory verify`

Print a summary of inventory state — useful before/after migration:

```bash
janus inventory verify
```

### `janus inventory migrate`

Import a Dashboard_For_Apis export JSON:

```bash
janus inventory migrate export.json
janus inventory migrate export.json --dry-run
janus inventory migrate export.json --verify
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `INVENTORY_ENCRYPTION_KEY` | *(unset)* | Fernet key for encrypting keys at rest |
| `INVENTORY_ENCRYPTION_PREVIOUS_KEY` | *(unset)* | Previous Fernet key, used once at startup to re-seal rotated credentials |
| `JANUS_ALLOW_INSECURE_DEV_KEY` | *(unset)* | Set to `1` to allow storing real credentials in plaintext when no encryption key is configured |
| `INVENTORY_PUSH_TOKEN` | *(unset)* | Bearer token for the push API |
| `INVENTORY_SCHEDULER_ENABLED` | `true` | Enable background recheck scheduler |
| `INVENTORY_CHECK_INTERVAL_HOURS` | `12` | Hours between scheduled rechecks |
| `VALIDATION_MAX_FAILURES` | `3` | Pause automatic validation after consecutive failures; manual recheck resumes it |
| `INVENTORY_SUBMIT_RATE_LIMIT` | `300` | Max keys per rate window (push/add) |
| `INVENTORY_SUBMIT_RATE_WINDOW_MS` | `60000` | Rate window in milliseconds |
| `INVENTORY_MIN_KEY_LENGTH` | `16` | Minimum accepted key length |
| `INVENTORY_MAX_KEY_LENGTH` | `512` | Maximum accepted key length |
| `INVENTORY_MAX_SUBMIT_BATCH` | `200` | Max keys per submit/push request |

## Export

Download inventory keys as JSON from the dashboard or:

```
POST /dashboard/api/inventory/export
```

The export (and the per-key `POST /dashboard/api/inventory/keys/{key_id}/json`
download) is POST-only so a decrypted credential can never be fetched by a
plain GET link, and query-param (`?key=`) authentication is not accepted on
these routes — use the `Authorization` header or the dashboard cookie.
Requires dashboard authentication when accessing remotely (see
[Dashboard — Authentication](dashboard.md#authentication)).
