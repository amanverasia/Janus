# OAuth credential write-back + Claude OAuth inventory and usage probe (#251)

Date: 2026-10-01. Status: draft for review. Issue: #251 (Cursor half removed separately in #253).

## Goal

1. Onboard `claude_oauth` as an inventory provider so Claude subscription accounts appear in the
   keys table, get validated, get an account-value probe (`/api/oauth/usage`), and feed routing
   soft-ordering through the existing `FallbackHandler.load_probed_headroom` path.
2. Make OAuth credentials survive refresh: the live provider becomes the single refresher and
   persists every refreshed credential, for **all** OAuth executors (Claude, Codex, Kiro,
   Antigravity).

Success: a Claude Code `.credentials.json` dropped on Connect becomes a routable inventory account
whose 5h/7d windows show in the keys table and demote the account at ≥90%; a Codex/Claude account
keeps working across restarts after its refresh token has rotated.

## Current behavior (verified in code)

- Live executors (`ClaudeOAuthProvider`, `CodexProvider`, `KiroProvider`, `AntigravityProvider`)
  refresh in memory (`_ensure_token` → `apply_token_response`) and never persist the result.
  `credential_blob()` exists on all four; only the Kiro validator calls it.
- Inventory validators run every 12h (`INVENTORY_CHECK_INTERVAL_HOURS`). Codex and Antigravity
  refresh unconditionally and persist the result; Kiro refreshes only when `needs_refresh`.
- With rotating refresh tokens (Codex single-use; Claude rotates) the two refreshers consume each
  other's tokens, and after restart the live provider can start from a consumed refresh token.
- Claude Code files are explicitly rejected by Connect (`CLAUDE_CODE_UNSUPPORTED_ERROR`); bare
  `sk-ant-oat…` tokens are detected as `anthropic` API keys (`url_guard.detect_provider_from_key`).
- A `claude_oauth` gateway provider (prefix `claude`) has its key mirrored to `upstream_keys` under a
  custom inventory id `claude` (`provider_key_sync`), with no validator or probe.

## Design

### A. Credential store hook (all OAuth executors)

New module `src/janus/inventory/credential_store.py`:

```python
class CredentialStore:
    def __init__(self, db_path, upstream_key_id): ...
    async def load(self) -> str | None          # decrypted current key_value, or None
    async def save(self, previous: str, current: str) -> bool  # CAS; True if written
```

- `save` runs in one transaction: `UPDATE upstream_keys SET key_value, key_hash, key_masked,
  updated_at WHERE id = ? AND key_hash = hash(previous)`. If the row's `source_node` is
  `gateway:<provider_id>`, it also updates `providers.api_key` for that provider, but only when the
  decrypted current `providers.api_key` equals `previous` (otherwise the user edited it; skip).
  Without this, `sync_provider_key` would later overwrite the refreshed row with the stale
  `providers.api_key`.
- `save` never changes status, validity, usability, or account-value columns. Failures are logged
  (no secrets) and swallowed. It never triggers `reload_providers`.
- Wiring: in `_reload_providers_locked`, after building a provider for a `ProviderConfig` with
  `upstream_key_id`, call `provider.attach_credential_store(CredentialStore(db_path, key_id))` when
  the provider has that method. Drivers and `ProviderConfig` stay unchanged. Directly configured
  gateway keys without an inventory row get no store and keep today's in-memory behavior.

Executor changes (Claude, Codex, Kiro, Antigravity), inside the existing refresh lock in
`_ensure_token`:

1. **Read-through:** if a store is attached, `load()` the stored blob. If its `expires_at` is later
   than the in-memory one (or the in-memory refresh token was rejected), adopt it and re-check
   `needs_refresh`. This picks up a refresh done by a validator while the provider was idle.
2. Refresh as today.
3. **Write-back:** on success, `await store.save(previous_blob, credential_blob())` as a
   fire-and-forget task (not on the request's critical path), held in a task set so it isn't
   garbage-collected.

### B. Validators refresh only when needed

- Codex: if `not needs_refresh(cred)` → probe the access token (`_probe_codex_access_token`) and
  return without refreshing. Refresh only for expired/missing access tokens.
- Antigravity: same rule (refresh only when `needs_refresh`).
- Kiro: already compliant.
- Claude (new `_validate_claude_oauth_key`): if `needs_refresh(cred)`, refresh once with
  `refresh_claude` and return the refreshed blob as `key_value`; otherwise no refresh. Then call
  `GET /api/oauth/usage` with the (possibly refreshed) access token as the auth check: 200 → usable;
  401/403 → invalid; 429/5xx/network → `probe_inconclusive`.

Net effect: in steady state only the live provider refreshes, and both refreshers re-read the
stored credential before using a refresh token.

### C. Claude OAuth inventory onboarding

- `catalog.py`: add an `inventory` block to `claude_oauth` (`billing_model: subscription`, base URL
  `https://api.anthropic.com`, `auth_type: oauth`), making `prefix_to_inventory_map` map
  `claude → claude_oauth`. Update catalog count tests.
- `inventory/claude_credentials.py::normalize_claude_credential` accepts Claude Code
  `.credentials.json` (`{"claudeAiOauth": {accessToken, refreshToken, expiresAt(ms), scopes,
  subscriptionType}}`), a flat `{access_token, refresh_token, expires_at}` object, or a bare
  `sk-ant-oat…` token. Output: Janus OAuth JSON (`access_token`, `refresh_token`, `expires_at` in
  seconds, `extra.subscriptionType`). Emails/account ids are dropped.
- `ingestion.py`: `claudeAiOauth` → `claude_oauth` (remove the rejection and
  `CLAUDE_CODE_UNSUPPORTED_ERROR`); add `claude_oauth` to `_TOKEN_CREDENTIAL_PROVIDERS` and the
  normalizer dispatch. Preview and submit share this path.
- `url_guard.detect_provider_from_key`: `sk-ant-oat` → `claude_oauth` before the generic
  `sk-ant-` → `anthropic` rule.
- `key_checker.validate_key`: dispatch `claude_oauth` to the new validator.
- **Migration** (idempotent, in `init_db`): re-point `upstream_keys.provider_id = 'claude'` →
  `'claude_oauth'` and delete the auto-created custom `inventory_providers` row `claude` only when
  its `routing_note = 'Mirrored from Providers page'`.
- Dashboard `ProvidersPage.svelte` credential hint and Connect docs mention the Claude Code file.
  Contract shapes regenerated (`providers`, `inventory` catalog shapes); size budgets checked.

### D. Claude usage probe

`_probe_claude_oauth` in `account_value.py`, registered in `ACCOUNT_VALUE_PROBES`:

- `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <access>` and
  `anthropic-beta: oauth-2025-04-20`, via the existing `_oauth_access_token` (expired → unavailable)
  and `_fetch_oauth_json` (401/403 → unavailable, key status untouched).
- Windows: `five_hour` → `5h`, `seven_day` → `weekly`, `seven_day_opus` → `weekly opus`,
  `seven_day_sonnet` → `weekly sonnet`; `utilization` (0–100) → `used_percent`, `resets_at` →
  `reset_at`. Unknown/null windows skipped; none recognized → transient `ProbeError`.
- Metadata: `subscription_type` from the stored credential's `extra` only. No emails/ids.
- Existing 10-minute TTL and per-key in-flight dedup apply. Anthropic rate-limits this endpoint; no
  per-request fan-out is added.

## Out of scope

- Probing directly configured (non-inventory) gateway Claude keys.
- Deduplicating a re-imported, already-rotated credential file against the refreshed row (hashes
  differ after rotation; the user gets a second row). Noted for follow-up.
- Interactive Claude OAuth login flow.

## Testing

- `credential_store`: CAS success/conflict, gateway-mirrored dual write, user-edited provider key
  skipped, no secrets in logs.
- Each executor: refresh writes back once; read-through adopts a newer stored blob; no store → no
  write; write-back failure does not fail the request.
- Validators: Codex/Antigravity skip refresh when the access token is valid; refresh when expired.
  Claude validator: 200 / 401 with refresh / 401 without / 429 / network.
- Normalizer + ingestion preview/submit for the Claude Code file, flat JSON, bare token, and that
  `sk-ant-api…` stays `anthropic`.
- Probe: respx fixtures for full/partial/empty usage, 401, expired token, no secret leakage.
- Migration: `claude` mirrored rows re-pointed; idempotent; non-mirrored custom `claude` provider
  untouched.
- Reload integration: a `claude_oauth` gateway provider + inventory key expands with
  `upstream_key_id`, gets a store attached, and probed windows demote it at ≥90%.
- Full gates: ruff, mypy, pytest, dashboard check, contract fixtures, docs strict, migration smoke.
