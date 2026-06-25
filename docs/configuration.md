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
| `require_api_key` | `bool` | `false` | When `true`, all `/v1/*` endpoints require auth (except `/health`) |
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
rates (in USD). These merge with and override the ~28 builtin model prices.

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
