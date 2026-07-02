# CLI Reference

Janus provides a `janus` command-line tool for server management, key
management, usage reporting, budget enforcement, and pricing lookup.

## Top-level commands

### `janus serve`

Start the gateway server.

```bash
janus serve --port 20128 --host 127.0.0.1
```

| Option | Default | Description |
|---|---|---|
| `--port` / `-p` | `20128` | Port to listen on |
| `--host` | `127.0.0.1` | Host to bind to |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |
| `--reload` | off | Enable auto-reload for development |

### `janus config-init`

Create a default config file. Will not overwrite an existing file.

```bash
janus config-init
# Config created: /home/user/.janus/config.yaml

janus config-init --path ./my-config.yaml
# Config created: /home/user/Projects/Janus/my-config.yaml
```

| Option | Default | Description |
|---|---|---|
| `--path` / `-p` | `~/.janus/config.yaml` | Where to create the config |

### `janus config-path`

Print the default config file path.

```bash
janus config-path
# /home/user/.janus/config.yaml
```

---

## Keys

### `janus keys create`

Create a new API key. The full key is printed once — save it immediately.

```bash
janus keys create --name "default"
```

```
API Key (save this — shown once): sk-janus-a1b2c3d4e5f67890a1b2c3d4e5f67890
ID: 1  Name: default
```

| Option | Default | Description |
|---|---|---|
| `--name` / `-n` | `default` | Name for this key |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus keys list`

List all API keys.

```bash
janus keys list
```

```
    1  sk-janus-a1b2...  default                 active  2026-06-25 10:00:00
    2  sk-janus-f3e4...  dev-key                 active  2026-06-25 11:30:00
    3  sk-janus-c5d6...  ci                       revoked  2026-06-24 09:00:00
```

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus keys revoke`

Revoke an API key by ID.

```bash
janus keys revoke 3
# Revoked key 3
```

| Argument | Description |
|---|---|
| `key_id` | Key ID to revoke (required) |

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

---

## Usage

### `janus usage stats`

Show overall usage statistics.

```bash
janus usage stats
```

```
Total requests: 1250
Total input tokens: 890000
Total output tokens: 320000

By model:
  claude-sonnet-4-20250514          800 requests     560000 in    200000 out
  gpt-4o                            450 requests     330000 in    120000 out
```

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus usage cost`

Show cost breakdown by model for the last N days.

```bash
janus usage cost --days 7
```

```
Cost breakdown (last 7 days):
Model                                  Requests         Cost
----------------------------------------------------------
  claude-sonnet-4-20250514                  800     $12.5000
  gpt-4o                                    450      $3.2000
----------------------------------------------------------
  Total                                             $15.7000
```

| Option | Default | Description |
|---|---|---|
| `--days` / `-d` | `30` | Number of days to show |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus usage by-key`

Show spending per client API key for the last N days.

```bash
janus usage by-key --days 7
```

```
Spending per key (last 7 days):
Key                                  Requests         Cost
------------------------------------------------
  default                                 1000     $13.5000
  dev-key                                  250      $2.2000
```

| Option | Default | Description |
|---|---|---|
| `--days` / `-d` | `30` | Number of days to show |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

---

## Budgets

### `janus budgets list`

List all active budgets.

```bash
janus budgets list
```

```
    1  Global            $10.00   spent:     $8.20      82%  warning
    2  Key #3             $5.00   spent:     $1.10      22%  ok
```

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus budgets set`

Create or update a budget. If a budget already exists for the given scope, it is
updated in place.

```bash
# Global budget: $10/day
janus budgets set --daily 10.00 --key global
# Budget 1 set: global daily limit = $10.00, warn at 80%

# Per-key budget for key named "dev-key": $5/day, warn at 70%
janus budgets set --daily 5.00 --key "dev-key" --warn 70
```

| Option | Default | Description |
|---|---|---|
| `--daily` / `-d` | *(required)* | Daily limit in USD (float) |
| `--key` / `-k` | `global` | `global` or a key name |
| `--warn` / `-w` | `80` | Warn threshold percentage |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

!!! note "Key matching"
    `--key` matches against key **names** (set with `janus keys create --name`),
    not numeric IDs. Use `global` for a global budget.

### `janus budgets delete`

Delete a budget by ID.

```bash
janus budgets delete 2
# Deleted budget 2
```

| Argument | Description |
|---|---|
| `budget_id` | Budget ID to delete (required) |

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

---

## Pricing

### `janus pricing list`

List all known model pricing, sorted by name. Includes builtin prices merged
with any DB overrides (seeded from YAML on first startup, then managed via
dashboard or CLI).

```bash
janus pricing list
```

```
  claude-3-5-haiku-20241022     in: $0.8    out: $4.0   cc: $1.0   cr: $0.08
  claude-sonnet-4-20250514      in: $3.0    out: $15.0  cc: $3.75  cr: $0.3
  gpt-4o                        in: $2.5    out: $10.0  cc: $0.0   cr: $0.0
  ...
```

Prices are per million tokens (Mtok).

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus pricing show`

Show pricing for a specific model.

```bash
janus pricing show gpt-4o
```

```
Model: gpt-4o
  Input:              $2.5 / Mtok
  Output:             $10.0 / Mtok
  Cache creation:     $0.0 / Mtok
  Cache read:         $0.0 / Mtok
```

| Argument | Description |
|---|---|
| `model` | Model name (required) |

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

Pricing uses progressive prefix matching — `gpt-4o` matches the same entry as
`gpt-4o-2024-08-06`. Unknown models show no pricing found.

---

## Inventory

Manage upstream API key inventory. See [Key Inventory](inventory.md) for concepts
and dashboard workflows.

### `janus inventory generate-encryption-key`

Generate a Fernet key for `INVENTORY_ENCRYPTION_KEY`:

```bash
janus inventory generate-encryption-key
# gAAAAABl...
```

### `janus inventory encrypt-keys`

Encrypt plaintext upstream keys at rest. Requires `INVENTORY_ENCRYPTION_KEY` to
be set in the environment.

```bash
export INVENTORY_ENCRYPTION_KEY='gAAAAABl...'
janus inventory encrypt-keys
```

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus inventory verify`

Summarize inventory state for cutover verification:

```bash
janus inventory verify
```

| Option | Default | Description |
|---|---|---|
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |

### `janus inventory migrate`

Import a Dashboard_For_Apis export JSON into upstream keys:

```bash
janus inventory migrate export.json
janus inventory migrate export.json --dry-run
janus inventory migrate export.json --verify
```

| Argument | Description |
|---|---|
| `export_file` | Path to export JSON (required) |

| Option | Default | Description |
|---|---|---|
| `--dry-run` | off | Count rows without writing |
| `--verify` | off | Print summary after import |
| `--config` / `-c` | `~/.janus/config.yaml` | Path to config file |
