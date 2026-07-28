# Ollama Cloud Provider Design

**Date:** 2026-07-28

## Goal

Add Ollama Cloud as a first-class Janus provider across the gateway catalog,
upstream-key inventory, routing, dashboard, documentation, and tests.

Ollama Cloud exposes an OpenAI-compatible model list at
`https://ollama.com/v1/models` and authenticated chat completions at
`https://ollama.com/v1/chat/completions`. Janus will use that transport rather
than add a second Ollama-native upstream executor.

## Provider Identity

- Unified catalog key, gateway ID, and inventory ID: `ollama`
- Display name: `Ollama Cloud`
- Routing prefix: `ollama`
- API type: `openai_compat`
- Base URL: `https://ollama.com/v1`
- Authentication: `Authorization: Bearer <OLLAMA_API_KEY>`
- Environment variable: `OLLAMA_API_KEY`
- Billing model: `subscription`
- Direct provider: yes
- Capabilities: vision, tool use, and reasoning
- Dashboard icon: llama emoji, with no new static logo asset

Clients address models as `ollama/<model>`, for example
`ollama/gpt-oss:20b`.

## Catalog and Dashboard

The provider is defined once in `src/janus/catalog.py` with both `inventory`
and `gateway` blocks. The existing derived dashboard and inventory catalogs
then expose it automatically.

The gateway entry uses an empty default-model list because Ollama Cloud's model
catalog changes as models are introduced and retired. Dashboard setup uses the
existing OpenAI-compatible Fetch Models action to populate models from
`/v1/models`. The existing Test Connection action sends an authenticated,
one-token chat completion.

The provider appears near OpenRouter in gateway ordering because both expose
multiple model families through one account, while Ollama remains marked as a
direct hosted service rather than an aggregator.

## Inventory Validation

Ollama Cloud's `/v1/models` response is public, so a successful model-list
request does not prove that a supplied API key is valid. Treating it as a
normal model-list-validated provider would incorrectly accept arbitrary keys.

Inventory validation therefore:

1. Fetches and records the OpenAI-compatible model list.
2. Sends an authenticated chat probe using `gpt-oss:20b` with a one-token
   output limit.
3. Accepts the key only when that chat request authenticates successfully.
4. Reuses the same inexpensive model for scheduled usability checks.

Ollama does not document a unique API-key prefix, so static prefix detection is
not added. Auto-detection discovers Ollama keys through the authenticated probe,
and users can always choose Ollama Cloud explicitly during import.

The inventory-to-gateway mapping is identity-based (`ollama` to `ollama`).
Existing provisioning and key expansion then create or enable the routing
provider and expose each routable inventory key as a fallback account.

## Request Flow

All client formats continue through Janus's canonical boundary:

1. Parse the client request into a canonical request.
2. Resolve `ollama/<model>` through the provider registry.
3. Build an OpenAI Chat Completions payload.
4. Send it with the existing shared `OpenAICompatProvider`.
5. Parse the response back into the canonical response.
6. Emit the client's requested format and record usage.

No format adapter imports a provider, and no provider imports a format adapter.
No new provider executor or API type is required.

## Errors, Fallback, and Accounting

Existing upstream handling applies unchanged:

- Authentication failures cool the account down and allow fallback.
- Rate limits, server errors, and network failures use the existing cooldown
  durations and retry ordering.
- Streaming requests are not replayed after partial output.
- Multiple Ollama keys participate in normal multi-account routing.
- Provider and client model allowlists apply normally.

Ollama plans measure cloud usage primarily by GPU utilization rather than a
published per-token price. Janus records tokens but assigns no built-in dollar
price for Ollama models. Users may add pricing overrides or soft request/token
quotas, but those do not exactly reproduce Ollama's five-hour and weekly usage
limits.

## Documentation

Update:

- `README.md` to replace stale hard-coded provider counts and mention Ollama
  Cloud where providers are summarized.
- `docs/providers.md` with dashboard, YAML, base URL, prefix, and model-fetch
  guidance.
- `docs/inventory.md` to list Ollama Cloud as a supported inventory provider
  and explain authenticated validation.

## Testing

Tests use mocked HTTP responses and require no live Ollama account or API key.

- Catalog tests verify the new unified, gateway, and inventory entries and
  updated counts/order.
- Inventory tests verify that a public model list alone cannot validate a key,
  a successful authenticated chat probe records models, and a 401 rejects the
  key.
- Provisioning/routing tests verify identity mapping and `ollama/<model>`
  resolution from inventory-backed accounts.
- Dashboard tests verify that Ollama Cloud appears in the provider catalog and
  model fetching parses the OpenAI-compatible response.
- The provider matrix verifies that the catalog entry builds and resolves
  through the existing OpenAI-compatible executor.
- Targeted pytest, Ruff, formatting, mypy, and strict MkDocs checks provide
  final verification.

## Out of Scope

- A native upstream transport using `https://ollama.com/api/chat`
- Ollama web-search or web-fetch endpoints
- Automatic synchronization of Ollama GPU-time plan limits
- Built-in per-token prices for subscription usage
- Live API calls in the test suite
