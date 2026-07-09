# API Key Scopes Design

> **Date:** 2026-07-09
> **Status:** Approved — implementing

## Goal

Give DB-managed Janus API keys (`sk-janus-*`) per-key policy so operators can:

1. Create **API-only** keys that cannot log into the dashboard
2. Restrict keys to an **allowlist of models** (exact IDs and `prefix/*` wildcards)
3. Optionally attach a **daily spend budget** when creating/updating a key (reuses existing budgets)

Existing keys keep full access by default. YAML static `config.api_keys` remain unrestricted.

## Decisions

| Topic | Choice |
|-------|--------|
| Storage | Columns on `api_keys` (`can_login`, `allowed_models`) |
| Empty allowlist | `NULL` or `[]` → allow all models |
| Login default | `can_login = 1` (opt out for API-only) |
| Model matching | Exact model/combo ID **or** `prefix/*` wildcard |
| Budgets | Existing `budgets.key_id` table; optional on key create/update |
| YAML static keys | Unrestricted (`can_login=True`, no model filter) |

## Data model

```sql
-- additions to api_keys
can_login INTEGER NOT NULL DEFAULT 1
allowed_models TEXT  -- JSON array of patterns, or NULL
```

Examples of `allowed_models`:

- `NULL` / `[]` → unrestricted
- `["openai/gpt-4o", "anthropic/claude-sonnet-4"]` → exact IDs
- `["openai/*", "my-combo"]` → prefix wildcard + combo name

Migration: idempotent `ALTER TABLE` via `PRAGMA table_info`, same pattern as usage columns.

## Enforcement

```
Request with API key
  → authenticate_api_key
  → load can_login + allowed_models onto request.state
  → Dashboard?
       yes → require can_login; else 401 / redirect / login error
       no  → after parse_request: model_allowed(model, allowlist)?
              no  → 403 model_not_allowed
              yes → existing budget check → route
```

### Dashboard

- `require_dashboard_access`: after successful API-key auth, reject if `can_login` is false
- `login_submit` API-key path: same check; do not set cookie; error message: "This API key cannot access the dashboard"
- Loopback bypass and username/password login unchanged

### API

- `_handle`: after `parse_request` (and thinking-suffix strip so the checked model is the resolved one), reject disallowed models with `403` and `error.type = "model_not_allowed"`
- `GET /v1/models`: filter listed IDs with the same matcher when allowlist is non-empty
- Combo names must be listed explicitly (or match a pattern) to be allowed

### Matching rules

```
model_allowed(model, allowed):
  if allowed is None: return True
  for pattern in allowed:
    if pattern == model: return True
    if pattern.endswith("/*") and model.startswith(pattern[:-1]): return True
  return False
```

`prefix/*` matches `prefix/foo` but not `prefix` alone and not `prefixother/foo`.

## Surfaces

### Storage / CLI

- `create_key(..., can_login=True, allowed_models=None)`
- `update_key(key_id, ...)`
- `get_key_policy(key_id)` / richer verify path for auth
- `janus keys create --name X [--no-login] [--models a,b,openai/*] [--daily-budget 5]`
- `janus keys update <id> [--login/--no-login] [--models ...] [--clear-models] [--daily-budget]`
- `janus keys list` shows login + models summary

### Dashboard Keys page

- Create: name, login checkbox (default on), models textarea (empty = all), optional daily budget
- Table: login yes/no, models summary
- Update endpoint for policy + optional budget
- Revoke unchanged; plaintext shown once on create

## Out of scope

- Scopes on YAML `config.api_keys`
- Fine-grained dashboard permissions (e.g. keys that can edit providers but not settings)
- Per-key rate limits beyond daily $ budget
- Deny-list semantics (only allowlists)

## Compatibility

- Existing DB keys: migration defaults → `can_login=1`, `allowed_models=NULL`
- New keys without flags: same as today
- Per-key budgets already enforced in `_handle`; this feature only makes them easier to set from key create/update
