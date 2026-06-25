# Budgets

Budgets are daily spending limits that cap how much can be spent per API key or
globally. They reset at midnight local time. Budgets are enforced before routing
— if a budget is exceeded, the request never reaches a provider.

Budgets are **not** defined in YAML. They are managed at runtime via the CLI or
the dashboard.

## Per-key vs global

| Scope | Config | Behavior |
|---|---|---|
| **Per-key** | `key_id` set to a specific key | Applies to requests authenticated with that key |
| **Global** | `key_id = NULL` | Applies to **all** requests, regardless of key |

When both a per-key and a global budget exist, **the most restrictive one wins**
— if either is exceeded, the request is blocked.

## Thresholds

| Threshold | Default | What happens |
|---|---|---|
| **Warn** | 80% | Request proceeds normally. Dashboard shows an amber warning. |
| **Hard** | 100% | Request rejected with `HTTP 429` + `Retry-After` header. |

The `Retry-After` header is set to the number of seconds until midnight, so
clients know exactly when spending resets.

### Rejection response

```json
{
  "error": {
    "message": "Daily budget exceeded. Spent $9.87 of $10.00 limit. Resets at midnight.",
    "type": "budget_exceeded",
    "today_spend": 9.87,
    "daily_limit": 10.00
  }
}
```

### Fail-safe

Database errors **never block requests**. If the budget check fails (e.g.
database is locked, schema error), the request proceeds as if no budget exists.

## Budget status values

| Status | Condition |
|---|---|
| `ok` | Spend below the warn threshold |
| `warning` | Spend at or above the warn threshold but below 100% |
| `exceeded` | Spend at or above 100% of the daily limit |

## CLI management

### List all budgets

```bash
janus budgets list
```

```
    1  Global            $10.00   spent:     $8.20      82%  warning
    2  Key #3             $5.00   spent:     $1.10      22%  ok
```

### Create or update a budget

```bash
# Global budget: $10/day, warn at 80%
janus budgets set --daily 10.00 --key global

# Per-key budget: $5/day for key named "dev-key", warn at 70%
janus budgets set --daily 5.00 --key "dev-key" --warn 70
```

```
Budget 1 set: global daily limit = $10.00, warn at 80%
```

| Option | Default | Description |
|---|---|---|
| `--daily` / `-d` | *(required)* | Daily limit in USD (float) |
| `--key` / `-k` | `global` | `global` or a key name |
| `--warn` / `-w` | `80` | Warn threshold percentage |

If a budget already exists for the given scope, it is updated in place.

### Delete a budget

```bash
janus budgets delete 2
```

```
Deleted budget 2
```

## Dashboard management

The **Budgets** page at `/dashboard/budgets` provides a live view of all budgets
with their current status (ok / warning / exceeded), spent amount, and
percentage bar.

- **Create** budgets via the HTMX form (select key scope, enter daily limit and
  warn percentage).
- **Delete** budgets via the revoke button on each row.

Changes take effect immediately — no server restart needed.

## See also

- [CLI reference](cli.md#budgets) — full `janus budgets` command details
- [Dashboard](dashboard.md#budgets) — the budgets page
- [Configuration](configuration.md) — YAML config reference (budgets are
  runtime-only, not in YAML)
