# Inventory history maintenance

Janus records an inventory history row only when an upstream key changes status. Each row's
optional balance is a snapshot captured at that transition; it is not a balance delta or a
credit-only event.

Older databases may contain repeated rows where `previous_status` and `new_status` are equal.
Janus hides those legacy rows from the dashboard, but does not delete them automatically.

## Removing legacy no-op rows

This cleanup is optional and destructive. Schedule a maintenance window, stop Janus and any
inventory-check workers, and make a tested backup of `janus.db` before continuing.

First, inspect the number of affected rows:

```sql
SELECT COUNT(*) AS no_op_rows
FROM upstream_key_history
WHERE previous_status = new_status;
```

Run the deletion only after confirming the count and backup:

```sql
BEGIN IMMEDIATE;

DELETE FROM upstream_key_history
WHERE previous_status = new_status;

SELECT changes() AS deleted_rows;
COMMIT;
```

Restart Janus, open the inventory dashboard, and verify that Recent Activity contains only real
status transitions. Keep the backup until the result has been checked.

`VACUUM` is not part of this cleanup. It requires additional free disk space and exclusive access,
so consider it separately if reclaiming the database file's unused pages is necessary.
