# Provider and model catalog

## Goal

Give Janus the provider and model ergonomics of a mature local router without
weakening its role as a shared, multi-tenant gateway.

## Invariants

- Provider presets are metadata and defaults. Provider instances are persisted
  operator configuration and credentials.
- Credentials, decrypted secrets, and upstream account identifiers never appear
  in ordinary dashboard or model-catalog responses.
- Model visibility is not authorization. Client API-key allowlists are enforced
  after request parsing and before routing.
- Provider-wide `allowed_models` remains a routing restriction. A provider's
  `selected_models` is a presentation preference only.
- Discovered model entitlements are stored per upstream credential. Catalog
  aggregation may deduplicate public model names, but routing must retain account
  eligibility.
- The last successful discovery result remains available when a later refresh
  fails. A transient provider failure must not erase the known catalog.
- A successful empty discovery is authoritative and clears stale entitlements;
  it is not treated as an unknown or failed discovery.
- Custom models participate in the same catalog and visibility flow as configured
  and discovered models.
- Provider executors are selected through the driver registry. New presets that
  speak an existing protocol must not require another executor implementation.
- Provider reload closes clients that were replaced, disabled, or deleted.
- In-flight requests and streams keep their provider generation until completion;
  retired executors close only after that generation drains.

## Model sources

The effective provider catalog is the union of:

1. configured provider models;
2. successful upstream discovery results for eligible accounts; and
3. enabled operator-defined custom models.

Each model retains its source and provider namespace. Provider selection controls
listing visibility, while the requesting Janus key applies a final exact or
`prefix/*` filter.

## Routing semantics

Namespaced requests use `provider/model`. A bare provider name may resolve its
configured default model. A bare known model may resolve across providers using
normal fallback ordering. Disabled providers are excluded. Hidden models remain
directly routable when provider and client authorization allow them, matching the
separation between visibility and routing.

## Management surface

The dashboard uses authenticated management APIs for provider presets, the
effective model catalog, visibility updates, and custom-model CRUD. Those APIs
return stable public provider identifiers and model metadata only. They never
return decrypted credentials or inventory account IDs.

A login-capable key is a full gateway operator. Client model allowlists apply to
gateway traffic and public discovery, not to global dashboard mutations.
