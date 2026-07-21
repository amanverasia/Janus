# Configuration

Janus reads its configuration from a YAML file at `~/.janus/config.yaml`. You can
print the exact path with:

```bash
janus config-path
```

Generate a template with all sections commented out:

```bash
janus config-init
```

## DB-driven configuration

On **first startup**, Janus seeds SQLite from your YAML file:

| YAML section | Seeded into |
|---|---|
| `providers` | `providers` table |
| `combos` | `combos` table |
| `token_savers` | `settings` table (`saver_*` keys) |
| `pricing` | `pricing_overrides` table |
| `server.require_api_key` | `settings` table (`server_require_api_key`) |

Seeding is **idempotent** — if a table already has rows, that section is skipped.
After seeding, the **database is the source of truth**. Editing YAML and restarting
will not re-apply providers, combos, savers, or pricing.

To manage runtime config:

- Use the [dashboard](dashboard.md) — changes hot-reload immediately
- **Export Config** (`GET /dashboard/api/export`) — download current DB state as YAML
- **Reset to Defaults** (Settings page) — wipe DB tables and re-seed from YAML

Static `api_keys` in YAML are **not** seeded — they remain in the config file and
are checked at auth time alongside DB-managed keys. YAML static keys always have
full access (dashboard login + all models). Scopes (`can_login`, `allowed_models`)
apply only to DB-managed `sk-janus-*` keys — see [API Reference](api-reference.md)
and the Keys dashboard page.

Gateway rate limiting is optional and disabled by default. When enabled, each DB key,
static YAML key, or client IP (when authentication is disabled) gets an independent
60-second sliding-window bucket. Counters are in memory, process-local, and reset on
restart. `/v1/health`, `/api/version`, and dashboard routes are exempt.

Runtime settings stored in the `settings` table:

| Key | Description |
|---|---|
| `server_require_api_key` | `true` / `false` — overrides YAML default |
| `server_gateway_rate_limit_rpm` | Per-client gateway requests per minute; `0` disables limiting |
| `saver_rtk_enabled` | RTK token saver on/off |
| `saver_caveman_enabled` | Caveman saver on/off |
| `saver_ponytail_enabled` | Ponytail saver on/off |
| `saver_ponytail_level` | `lite`, `full`, or `ultra` |

## Environment Variable Resolution

Any value in the YAML may reference environment variables using the `${VAR_NAME}`
syntax. Variables are resolved at startup — if the variable is unset it expands to
an empty string.

```yaml
providers:
  - id: openai
    api_key: ${OPENAI_API_KEY}
```

Set them in your shell or `.env`:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## `server`

Controls the HTTP server.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `port` | `int` | `20128` | Server port |
| `host` | `str` | `"127.0.0.1"` | Bind address |
| `require_api_key` | `bool` | `false` | When `true`, all API endpoints require auth (except `/health`). Can be toggled at runtime from the dashboard Settings page (stored in DB). |
| `data_dir` | `Path` | `~/.janus` | Directory for the SQLite DB (`data_dir/janus.db`) |

```yaml
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false
  data_dir: ~/.janus
```

!!! tip "Binding to all interfaces"
    Set `host: 0.0.0.0` to accept connections from other machines on your network.
    Always enable `require_api_key` when doing so.

---

## `providers`

A list of provider configurations. Each entry registers one upstream account.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | *(required)* | Unique provider/account identifier |
| `prefix` | `str` | *(required)* | URL routing prefix. Multiple configs can share a prefix for multi-account. |
| `api_type` | `str` | *(required)* | One of: `openai_compat`, `anthropic`, `gemini`, `opencode_free` |
| `base_url` | `str` | *(required)* | Upstream API base URL |
| `api_key` | `str \| None` | `None` | API key for the upstream provider |
| `models` | `list[str]` | `[]` | Supported model suffixes (used without the prefix) |

```yaml
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

!!! note "Model naming"
    Clients reference models as `{prefix}/{model}` — for example `openai/gpt-4o`.
    The `models` list contains suffixes only (`gpt-4o`), not the full prefixed name.

See [Providers](providers.md) for per-provider setup guides and the full list of
supported `api_type` values.

---

## `combos`

Named ordered model sequences. A client sends the combo name as the `model` value
and Janus tries each model in order with all its accounts.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | *(required)* | Combo name (client sends this as the `model` value) |
| `models` | `list[str]` | *(required)* | Ordered list of `{prefix}/{model}` strings to try |

```yaml
combos:
  - name: best-effort
    models:
      - anthropic/claude-sonnet-4-20250514
      - openai/gpt-4o
      - gemini/gemini-2.5-pro
```

In this example, Janus first tries `claude-sonnet-4-20250514` on every Anthropic
account. If all are rate-limited or down, it falls back to `gpt-4o`, then
`gemini-2.5-pro`.

---

## `api_keys`

A list of static API key strings. These are checked at auth time in addition to
DB-managed keys created via the CLI or dashboard.

```yaml
api_keys:
  - sk-janus-my-static-key
  - sk-janus-another-key
```

When `server.require_api_key` is `true`, requests must include one of these keys
(or a DB-managed key) in the `Authorization: Bearer <key>` header. See
[API Reference - Authentication](api-reference.md#authentication) for details.

!!! tip
    DB-managed keys (created with `janus keys create`) are the recommended way to
    manage keys — they support per-key usage tracking and budgets. Static keys in
    the config are simpler for single-user setups.

---

## `token_savers`

Configures token-saving transformations applied to each request after parsing and
before provider routing. Savers stack — all enabled ones run in sequence.

| Saver | Field | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `rtk` | `enabled` | `bool` | `true` | Compresses tool output (git diffs, file listings, logs) |
| `caveman` | `enabled` | `bool` | `false` | Prepends a terse-output system prompt |
| `ponytail` | `enabled` | `bool` | `false` | Prepends a lazy-dev system prompt |
| `ponytail` | `level` | `str` | `"full"` | Ponytail verbosity: `"lite"`, `"full"`, or `"ultra"` |

```yaml
token_savers:
  rtk:
    enabled: true          # default — compresses tool_result content
  caveman:
    enabled: false
  ponytail:
    enabled: false
    level: full            # lite | full | ultra
```

!!! info "Fail-safe"
    Savers are fail-safe — if a saver throws an exception it is caught and logged,
    never breaking the request.

---

## `pricing`

User pricing overrides. Keys are model names; values are dicts with per-million-token
rates (in USD). On first startup, this section seeds the `pricing_overrides` table.
After that, manage overrides via the dashboard Pricing page or CLI — the YAML section
is not re-read on restart.

| Rate Key | Description |
|----------|-------------|
| `input_per_mtok` | $ per million input tokens |
| `output_per_mtok` | $ per million output tokens |
| `cache_creation_per_mtok` | $ per million cache-creation tokens |
| `cache_read_per_mtok` | $ per million cache-read tokens |

```yaml
pricing:
  my-custom-model:
    input_per_mtok: 1.0
    output_per_mtok: 3.0
    cache_creation_per_mtok: 0.0
    cache_read_per_mtok: 0.25
  gpt-4o:
    input_per_mtok: 2.5
    output_per_mtok: 10.0
    cache_creation_per_mtok: 0.0
    cache_read_per_mtok: 1.25
```

!!! note "Unknown models"
    Models not found in builtin pricing or overrides cost `$0.0` — this is not an
    error. Add an override if you need cost tracking for an unlisted model.

    Pricing uses progressive prefix matching: `claude-sonnet-4-20250514` will match
    a key `claude-sonnet-4` if the full name isn't present.

---

## Full Example

```yaml
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false
  data_dir: ~/.janus

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

  - id: gemini
    prefix: gemini
    api_type: gemini
    base_url: https://generativelanguage.googleapis.com
    api_key: ${GEMINI_API_KEY}
    models: [gemini-2.5-pro, gemini-2.0-flash]

combos:
  - name: best-effort
    models:
      - anthropic/claude-sonnet-4-20250514
      - openai/gpt-4o
      - gemini/gemini-2.5-pro

api_keys:
  - sk-janus-my-static-key

token_savers:
  rtk:
    enabled: true
  caveman:
    enabled: false
  ponytail:
    enabled: false
    level: full

pricing:
  my-custom-model:
    input_per_mtok: 1.0
    output_per_mtok: 3.0
    cache_creation_per_mtok: 0.0
    cache_read_per_mtok: 0.25
```
