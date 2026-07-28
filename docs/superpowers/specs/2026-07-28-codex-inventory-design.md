# Codex Inventory Credentials Design

**Date:** 2026-07-28

## Goal

Let users paste Codex / ChatGPT OAuth credentials into **Key Inventory**, select
**Codex** as the provider, validate them, and route `codex/<model>` through the
existing `CodexProvider` executor — including multi-account fallback — without
breaking today’s Providers-page paste workaround.

## Non-goals (this pass)

- Dashboard PKCE / “Connect ChatGPT” OAuth (Copilot-style Connect)
- Full 9router backup import (settings, combos, aliases, non-Codex providers)
- Upstream `wham/usage` quota sync or rolling 5h windows
- Persisting rotated tokens from the request path back into inventory (follow-up;
  refresh remains in-memory for the process lifetime as today)
- Changing Codex request transforms, headers, or Responses allowlists

## Current state

- Gateway-only catalog entry `codex` (`api_type: codex`, prefix `codex`).
- `CodexProvider` calls `POST …/responses` with OAuth Bearer tokens and optional
  `chatgpt-account-id` from `workspaceId` / `extra.workspaceId`.
- Credentials live in `providers.api_key` as a bare access token or JSON blob
  parsed by `oauth_tokens.parse_credential` (already accepts camelCase
  `accessToken` / `refreshToken`).
- Inventory rejects values over `INVENTORY_MAX_KEY_LENGTH` (default **512**).
  Real Codex JWTs are ~1.6–1.8KB; a 9router connection object is several KB.
- Inventory ingestion currently strips `\n` / `\t` from key values, which would
  corrupt pretty-printed JSON unless we special-case credential blobs.

## Credential shapes

### Canonical Janus blob (stored in `upstream_keys.key_value`)

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1750000000,
  "extra": { "workspaceId": "<chatgpt-account-uuid>" }
}
```

Bare access tokens remain valid (same short-lived fallback as the Providers path).

### Accepted paste inputs

On ingest (when provider is `codex`, or when the pasted text is clearly a Codex
credential object), normalize into the canonical blob:

1. **Janus credential JSON** — pass through `parse_credential`; keep as-is if
   already snake_case.
2. **9router `providerConnections` row** (or a slim subset) with fields such as
   `accessToken`, `refreshToken`, `expiresAt`, `idToken`,
   `providerSpecificData.chatgptAccountId`. Map:
   - `accessToken` → `access_token`
   - `refreshToken` → `refresh_token`
   - `expiresAt` (ISO-8601) → `expires_at` (unix seconds)
   - `providerSpecificData.chatgptAccountId` → `extra.workspaceId`
   - Optionally retain `idToken` under `extra.id_token` if present (unused by
     the executor today; harmless)
3. **Bare access token** string.
4. **Batch paste:** a JSON **array** of connection objects, or a wrapper object
   with a `providerConnections` array. Each Codex (`provider == "codex"` or
   missing provider when user selected Codex) entry becomes one inventory row.
   Non-Codex entries in a mixed array are skipped with a clear per-item status.
   Full backup keys (`settings`, `combos`, …) are ignored; only connection
   objects are ingested.

Labels: prefer connection `name` / `email` when present; otherwise leave null.

## Catalog

Add an `inventory` block to the existing `codex` catalog entry (gateway
unchanged):

- `id` / `name`: `codex`
- `display_name`: `Codex (ChatGPT)`
- `base_url`: `https://chatgpt.com/backend-api/codex`
- `auth_type`: `oauth` (or existing inventory vocabulary closest to OAuth blobs)
- `billing_model`: `subscription`
- `is_direct`: `true`
- `models_endpoint` / `health_check_endpoint` / `credit_check_endpoint`: `null`
  (no public key-scoped `/models`; validation is custom)
- Routing note: paste Janus blob, 9router connection JSON, or bare token; select
  Codex explicitly (no JWT auto-detect required in v1)

Inventory ↔ gateway mapping remains identity (`codex` → `codex`). Existing
`ensure_routing_providers` / `expand_gateway_provider` create or enable the
gateway row and expand each routable inventory key as
`{provider_id}::uk_{key_id}` with `api_key` set to the stored blob.

## Ingestion changes

1. **Length:** For Codex credential blobs (JSON starting with `{` / `[`, or
   provider `codex`), allow up to at least **16 KiB** per value (env override
   still respected via a higher default or a Codex-specific cap). Short API-key
   providers keep today’s default unless a global raise is cleaner and
   harmless.
2. **Whitespace:** Do **not** strip internal newlines/tabs for JSON credential
   pastes; only trim outer whitespace. Compact after successful parse before
   storage so equality / dedupe stays stable.
3. **Normalization helper** (e.g. `normalize_codex_credential(raw) -> str`)
   shared by inventory ingest and reusable by tests; Providers-page paste path
   may call the same helper optionally but must remain able to accept raw blobs
   exactly as `CodexProvider` does today.
4. **Dedupe:** Prefer matching on refresh token or normalized access token, not
   the entire pretty-printed JSON string, so re-pasting the same account after
   a token refresh updates rather than duplicating when practical. If a unique
   index only exists on full `key_value`, update-in-place on same refresh token
   / account id when detectable; otherwise document “exists” on identical
   stored blob.

## Validation

Codex has no inventory-friendly authenticated `/models` list for ChatGPT
accounts. Validation for `provider_id == "codex"`:

1. Normalize the paste to a canonical blob.
2. If a refresh token is present, call existing `refresh_codex()`. Success →
   valid; store the **post-refresh** canonical blob (updated access token /
   expiry) as `key_value`.
3. If refresh is missing or fails but an access token is present, optionally
   probe `POST /responses` with a **known ChatGPT-account-safe** tiny request
   (model from gateway `default_models`, e.g. `o4-mini` or `gpt-5.1-codex`,
   minimal input, `store=false`). Treat 401/403 as invalid; treat other 4xx
   that clearly indicate auth success but bad model as valid only if we cannot
   pick a safe probe model — prefer refresh-only validation when a refresh
   token exists to avoid model-support false negatives (9router exports show
   400 “model not supported” for some ids).
4. Scheduled rechecks: refresh-token path preferred; same rules as above.

Do not add Codex to generic model-list or chat-completion validator sets used
for OpenAI-compat providers.

## Routing and executors (preserve workarounds)

- `CodexProvider`, `oauth_tokens.refresh_codex`, Responses format routing, and
  Providers UI OAuth hint remain as they are.
- Inventory-expanded accounts pass the stored blob as `ProviderConfig.api_key`;
  `_build_provider` already constructs `CodexProvider(api_key=…)`.
- Manual Providers rows with a pasted blob continue to work **alongside**
  inventory-backed accounts (same prefix `codex`); no requirement to migrate
  existing rows.
- Do not change cooldown, fallback, or subscription `$0` pricing behavior.

## Dashboard / docs UX

- Inventory provider dropdown includes Codex.
- Paste textarea accepts one JSON object, an array, or a
  `{ "providerConnections": [ … ] }` fragment; multi-entry paste returns
  per-row ingest results like today’s batch import.
- Docs (`docs/inventory.md` and/or `docs/providers.md`): how to paste a 9router
  connection or Janus blob; note that full backup import is out of scope;
  remind that Providers-page paste still works.
- No Connect button in this pass.

## Error handling

- Invalid JSON → rejected with a clear message.
- Mixed backup with no Codex connections → all skipped / rejected with reason.
- Refresh failure with no usable access token → invalid.
- Length / batch limits → same inventory error channels as today.

## Testing

- Unit: normalize 9router connection → canonical blob (including
  `chatgptAccountId` → `workspaceId`); ISO `expiresAt` → unix `expires_at`.
- Unit: array / `providerConnections` expansion into multiple ingest entries.
- Unit: ingest length + whitespace preservation for multiline JSON.
- Unit/integration: `validate_key` for `codex` with mocked `refresh_codex`
  success/failure.
- Integration: provision + `expand_gateway_provider` yields multiple
  `codex::uk_*` configs whose `api_key` is the stored blob.
- Regression: existing `CodexProvider` / Providers paste tests still pass
  unchanged in behavior.

## Implementation sketch (files)

- `src/janus/catalog.py` — add `inventory` under `codex`
- `src/janus/providers/oauth_tokens.py` or small
  `src/janus/inventory/codex_credentials.py` — normalize + batch extract
- `src/janus/inventory/ingestion.py` — length/whitespace/normalize hooks
- `src/janus/inventory/key_checker.py` — Codex validation branch
- Dashboard inventory templates/docs if the provider list is hard-coded anywhere
- Tests under `tests/unit/inventory/` and related integration coverage

## Success criteria

1. User can paste one or many 9router-style Codex connection objects into Key
   Inventory, choose Codex, and get routable accounts.
2. `codex/<model>` requests use those accounts via existing fallback.
3. Existing Providers-page Codex paste and in-memory refresh still work.
4. Pretty-printed multi-KB JSON is not rejected solely for length or newlines.
