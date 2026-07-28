# Cooldown Override Controls Design

**Date:** 2026-07-28

## Goal

Give operators two ways to override account cooldowns:

1. A persistent **global toggle** to disable cooldown enforcement (and new
   cooldown marks) until re-enabled.
2. A **Clear all cooldowns** action that immediately wipes active cooldown state
   (memory + SQLite) without changing the toggle.

Ship this as **Janus v2.0.0** because it changes a core routing safety default
surface (operators can deliberately bypass cooldown protection).

## Non-goals

- Per-provider or per-account cooldown disable
- Changing cooldown durations / backoff formulas
- Disabling inventory rate-limit deprioritization or subscription quota soft
  deprioritization (separate mechanisms)
- Env-only kill switch as the primary UX (optional later; dashboard/DB setting
  is the source of truth)

## Current behavior

- `FallbackHandler` stores cooldowns in memory (`_cooldowns`) and persists to
  SQLite via `storage/cooldowns.py`.
- On 429 / 5xx / auth / network errors, `_handle()` / fusion call
  `mark_cooldown(...)`.
- `resolve_attempts` skips cooled-down accounts; if all are cooled down, raises
  `AllAccountsCooledDown` (503 + Retry-After).
- Dashboard Routing and Overview surface active cooldown counts/timers; alerts
  include a cooldown warning.

## Setting: `server_cooldowns_enabled`

- Type: boolean string in the `settings` table (`"true"` / `"false"`).
- Default: `"true"` (cooldowns on — same as today).
- Added to `SERVER_SETTING_DEFAULTS` and `ensure_server_defaults`.
- Editable from:
  - Dashboard **Settings** (checkbox, same HTMX pattern as other server flags)
  - CLI `janus settings set server_cooldowns_enabled false|true` (add key to the
    allowlisted settings keys in `cli.py`)

### Semantics when `"false"`

1. **`is_available`** treats cooldown state as inactive (accounts are not
   skipped for cooldown reasons).
2. **`mark_cooldown`** is a no-op (does not mutate memory, backoff, or SQLite).
3. Existing SQLite cooldown rows are **left in place** until Clear or natural
   expiry; they have no routing effect while the setting is false. On re-enable
   (`"true"`), still-active rows apply again after `load_cooldowns` / in-memory
   map (reload or process already holding memory — see hot-apply below).
4. **`AllAccountsCooledDown`** should not fire solely due to cooldown while
   disabled.

### Hot-apply

Prefer reading the enabled flag from an in-memory attribute on
`FallbackHandler` (e.g. `cooldowns_enabled: bool = True`) updated when the
setting is saved (dashboard settings POST and CLI set), without requiring a
full process restart. On provider reload, preserve the flag via
`adopt_runtime_state` or re-read from settings after rebuild.

Fallback: if a code path cannot update the handler immediately, document that
a provider reload / restart applies the change — but the primary design is
immediate apply.

## Clear all cooldowns

### Behavior

- Clears `FallbackHandler._cooldowns` (and related backoff entries for those
  keys, or all backoff — prefer clearing both cooldown map and backoff map for
  a true reset).
- Deletes all rows from the `cooldowns` SQLite table (add
  `clear_all_cooldowns(db_path)` in `storage/cooldowns.py`).
- Does **not** change `server_cooldowns_enabled`.
- Works whether cooldowns are enabled or disabled (useful to wipe stale rows
  before re-enabling).

### UI

- Primary control on the **Routing** page (near the cooldown banner / overview),
  labeled **Clear all cooldowns**, with a brief confirm or immediate action +
  toast (follow existing dashboard mutation patterns).
- Optional secondary mention on Settings next to the toggle (“Clear active
  cooldowns now”) calling the same API.

### API

- `POST /dashboard/api/routing/cooldowns/clear` (auth: dashboard access) →
  clears state, returns HTML fragment or JSON success toast payload consistent
  with neighboring routes.

## Alerts and banners

- When `server_cooldowns_enabled` is false:
  - Routing page shows an info/warning banner: cooldowns are disabled; accounts
    will be retried without cooldown skips.
  - Dashboard cooldown **warning** alerts are suppressed (or downgraded to a
    single info alert “Cooldowns disabled”) so operators are not told accounts
    are blocked when they are not.
- When enabled and active cooldowns exist: existing warning behavior unchanged.

## Docs / changelog / version

- Document the setting and Clear action in `docs/` (routing or configuration /
  dashboard section).
- `CHANGELOG.md`: **2.0.0** — Added cooldown disable toggle + clear-all action;
  note that disabling removes a protective routing behavior.
- Bump `pyproject.toml` and FastAPI `version` to `2.0.0` as part of the release
  cut after merge (same release process as v1.8.0).

## Testing

- Unit: with `cooldowns_enabled=False`, `mark_cooldown` does not populate
  `_cooldowns`; `resolve_attempts` returns accounts that would otherwise be
  cooled down.
- Unit: `clear_all` empties memory + DB helper.
- Integration/dashboard: settings toggle persists; clear endpoint empties
  active cooldowns used by Routing snapshot.
- Regression: default `"true"` preserves existing cooldown tests without
  changes to durations/backoff math.

## Success criteria

1. Operator can turn cooldowns off from Settings/CLI and immediately route
   through previously cooled-down accounts.
2. Operator can clear all cooldowns without disabling the feature.
3. Default remains cooldowns **on**.
4. Released as **v2.0.0**.
