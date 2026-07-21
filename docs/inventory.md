# Key Inventory

Janus includes an **upstream key inventory** — a separate subsystem for storing,
validating, and routing with many API keys across 27+ providers. Keys are checked
for validity, credit balance (where supported), and model access, then wired into
gateway routing as multi-account pools.

Open the dashboard at `/dashboard/inventory` or use the CLI (`janus inventory`).

## How it works

1. **Add keys** — paste keys in bulk, import a JSON export, or push via API.
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

### Overview — `/dashboard/inventory`

Credit summary, provider cards, best keys, recent activity, and encryption status.

### Key list — `/dashboard/inventory/keys`

Filter by provider or status, search, sort, and paginate. Per-key actions:

- **Recheck** — re-validate a single key
- **Delete** — remove from inventory
- **Reclassify** — fix misidentified provider assignments (bulk action on overview)

### Add keys — `/dashboard/inventory/add`

Paste one or many keys (one per line). Optionally set a label, pick a provider, or
provide a custom base URL. Keys are auto-detected when provider is omitted.

### Import — `/dashboard/inventory/import`

Import a **Dashboard_For_Apis** JSON export. Use this when migrating from another
key-management tool.

## Supported inventory providers

Janus recognizes keys for these providers (auto-detection probes each):

OpenAI, Anthropic, OpenRouter, Google AI (Gemini), Groq, Together, Perplexity,
Cohere, Mistral, DeepSeek, xAI, Hugging Face, Replicate, Fireworks, NVIDIA,
Moonshot, DashScope (Qwen), MiniMax, SiliconFlow, StepFun, Zhipu, Xiaomi, Tavily,
Firecrawl, fal.ai, Exa, Brave Search, plus **custom** and **unidentified**
fallbacks.

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
| `INVENTORY_PUSH_TOKEN` | *(unset)* | Bearer token for the push API |
| `INVENTORY_SCHEDULER_ENABLED` | `true` | Enable background recheck scheduler |
| `INVENTORY_CHECK_INTERVAL_HOURS` | `12` | Hours between scheduled rechecks |
| `INVENTORY_SUBMIT_RATE_LIMIT` | `300` | Max keys per rate window (push/add) |
| `INVENTORY_SUBMIT_RATE_WINDOW_MS` | `60000` | Rate window in milliseconds |
| `INVENTORY_MIN_KEY_LENGTH` | `16` | Minimum accepted key length |
| `INVENTORY_MAX_KEY_LENGTH` | `512` | Maximum accepted key length |
| `INVENTORY_MAX_SUBMIT_BATCH` | `200` | Max keys per submit/push request |

## Export

Download inventory keys as JSON from the dashboard or:

```
GET /dashboard/api/inventory/export
```

Requires dashboard authentication when accessing remotely (see
[Dashboard — Authentication](dashboard.md#authentication)).
