# Budgets

Janus supports **daily spending limits** and **absolute per-key spending limits**,
measured in USD. Budgets are checked before routing: once a limit is reached,
subsequent requests are rejected before reaching a provider.

- **Daily:** spending during the current calendar day. Resets at midnight in the
  configured reporting timezone (`server_reporting_timezone`, UTC by default).
- **Absolute:** all recorded spending for a specific Janus API key. Never resets,
  including across midnight, restarts, or budget edits. Spending from before the
  limit was configured also counts.

An API key can have either limit or both. A global daily budget can additionally
limit spending across all keys. **Reaching any applicable limit blocks requests.**

Budgets are stored in SQLite, not YAML. Manage them with the CLI or the dashboard;
changes take effect without a server restart.

## Set an absolute budget

Give an existing key named `dev-key` a $25 total allowance:

```bash
janus budgets set --key "dev-key" --absolute 25
```

Or create a key with a lifetime cap, with or without a daily cap:

```bash
janus keys create --name "trial-key" --absolute-budget 25
janus keys create --name "limited-app" --daily-budget 5 --absolute-budget 25
```

For an existing key identified by its numeric ID:

```bash
janus keys update 3 --absolute-budget 25
```

If the key has already spent $8, setting an absolute limit of $25 leaves $17.
Setting a limit below its recorded spend blocks new requests immediately; it does
not grant a new allowance. To give that key $10 more after spending $25, increase
the limit to $35.

Absolute budgets apply to **SQLite-managed Janus client keys**, not provider or
inventory credentials. Static keys in YAML do not have individual database key
identities and cannot receive per-key budgets. Global daily budgets still apply
to their requests.

## Thresholds and enforcement

| Threshold | Default | What happens |
|---|---|---|
| Warning | 80% | Requests proceed. The dashboard shows a warning. |
| Reached | 100% | New requests are rejected with HTTP 429. |

The warning threshold applies to both limits on the key. A daily rejection
includes `Retry-After`, the number of seconds until the next reporting-day boundary.
An absolute rejection has **no `Retry-After`** because waiting does not replenish
it. Increase or remove the absolute limit to allow more requests.

### Absolute rejection response

```json
{
  "error": {
    "message": "Absolute budget exceeded. Spent $25.00 of $25.00 lifetime limit. This budget does not reset; increase or remove it to allow more requests.",
    "type": "budget_exceeded",
    "budget_period": "absolute",
    "total_spend": 25.0,
    "absolute_limit": 25.0,
    "resets_at": null
  }
}
```

Daily rejections retain `type: "budget_exceeded"`, `today_spend`, and `daily_limit`,
and explain the reporting timezone's midnight reset. If both limits are reached,
the absolute rejection takes precedence because a daily reset cannot restore access.

### Accounting limits

Budget amounts use Janus's **recorded costs**, based on configured model prices,
not a live upstream billing balance. Unknown or intentionally unpriced models
record $0 and therefore do not consume the budget. Configure prices for models
you want to meter. Explicit pricing backfills can change historical costs and
therefore change the measured lifetime total.

Like daily budgets, absolute budgets are admission checks, not prepaid balance
reservations. An accepted request or concurrent in-flight requests can take the
final recorded total above the cap. Requests already streaming are not interrupted.
Usage recording and budget checks retain Janus's existing fail-safe behavior:
database failures do not block requests.

Lifetime totals use the persistent `usage` history; clearing request logs does
not clear spending. Removing and re-adding a budget does not reset its total.
Deleting or altering usage records changes the accounting history, so retain that
history when relying on lifetime budgets.

## CLI management

### List budgets

```bash
janus budgets list
```

The output distinguishes daily limits and today's spend from absolute limits and
total spend. Budget status is `ok`, `warning`, or `exceeded`; when both limits
exist, it reflects whichever is more restrictive.

### Set or change limits

```bash
janus budgets set --key global --daily 10
janus budgets set --key "dev-key" --daily 5 --absolute 25 --warn 70
janus budgets set --key "dev-key" --absolute 35
```

Omitted limits stay unchanged. An omitted warning threshold keeps its existing
value, or defaults to 80% for a new budget.

| Option | Description |
|---|---|
| `--daily` / `-d` | Positive, finite daily limit in USD |
| `--absolute` | Positive, finite lifetime limit in USD; requires a specific key |
| `--key` / `-k` | Key name, or `global` for a daily gateway-wide limit |
| `--warn` / `-w` | Warning threshold from 1 to 100 percent |
| `--clear-daily` | Remove the daily limit without changing the absolute limit |
| `--clear-absolute` | Remove the absolute limit without changing the daily limit |

Provide at least one limit or clear option. You cannot set and clear the same
limit in one command.

### Remove limits

```bash
janus budgets set --key "dev-key" --clear-absolute
janus keys update 3 --clear-absolute-budget
janus keys update 3 --clear-daily-budget
```

Clearing both limits removes the budget. To delete the entire budget by its budget
ID, use `janus budgets delete 2`; this removes both limits, not the API key or usage.

## Dashboard management

- On **API keys**, create or edit a key and set **Daily budget (USD)**,
  **Absolute budget (USD)**, or both. The edit form displays the current values.
  A blank field means no limit; clearing it removes that limit.
- On **Budgets**, select **Set budget**. Both daily and absolute limit fields are
  visible immediately. Choose a specific API key under **Scope** to enable the
  absolute limit; **Global gateway (daily only)** does not support lifetime caps.
  If no keys exist, the dialog links to API-key creation. Existing limits load
  when selecting a scope or editing a row.
- **Spent today** uses the reporting timezone's calendar day. **Spent total**
  shows lifetime spending when an absolute limit is configured. Blank total values
  for daily-only budgets are displayed as an em dash, not as zero lifetime spend.
- At least one limit is required on the Budgets form. Use the row's delete button
  or clear both fields on the API-key edit form to remove the whole budget.

## See also

- [CLI reference](cli.md#budgets)
- [Dashboard](dashboard.md#budgets)
- [Configuration](configuration.md)
