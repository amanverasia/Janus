# Janus — Phase 2: Fallback & Combos (Design Spec)

**Status:** Approved
**Date:** 2026-06-24
**Builds on:** [Phase 1 Core Router](./2026-06-24-janus-phase1-core-router-design.md)

---

## 1. Goal

Evolve the routing layer from single-model resolution to multi-account rotation, named combo sequences, and rate-limit cooldown. After Phase 2, Janus can:

- Route a request through N API keys from N accounts for the same provider+model, automatically round-robining and cooling down rate-limited keys.
- Expand a named combo (ordered model sequence) and try each model in order until one succeeds.
- Back off individual accounts on 429/5xx/auth/network errors with per-error-type cooldown durations.

## 2. What Changes from Phase 1

| Component | Phase 1 | Phase 2 |
|-----------|---------|---------|
| `providers/registry.py` | One `ProviderConfig` per prefix | `list[ProviderConfig]` per prefix (multi-account) |
| `routing/fallback.py` | Single-model stub | Combo expansion + account selection + cooldown state |
| `config/schema.py` | No combos | `ComboConfig` model + `combos` list on `JanusConfig` |
| `api/routes.py` | One resolve → one call | Ordered attempt list → retry loop with cooldown |

Everything else (canonical model, format adapters, provider executors, streaming translator) is unchanged.

## 3. Combo Definitions

A combo is a named ordered list of model strings. Defined in `config.yaml`:

```yaml
combos:
  - name: my-coding-stack
    models:
      - glm/glm-4.7
      - an/claude-sonnet-4-20250514
      - oc/auto
```

When a client sends `"model": "my-coding-stack"`, Janus expands to the ordered list and tries each model (with all its accounts) before failing.

Combos are stored in `JanusConfig.combos: list[ComboConfig]` and registered in the registry on app startup.

## 4. Multi-Account

Multiple `ProviderConfig` entries with the **same prefix** but different `id` and `api_key` represent multiple accounts for the same provider. Example:

```yaml
providers:
  - id: ds-1
    prefix: ds
    api_type: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DS_KEY_1}
    models: [deepseek-chat, deepseek-reasoner]
  - id: ds-2
    prefix: ds
    api_type: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DS_KEY_2}
    models: [deepseek-chat, deepseek-reasoner]
```

Both serve `ds/deepseek-chat`. Selection order: config order (first available, skip cooled-down).

## 5. Fallback Triggers & Cooldown

| Error | Action | Default Cooldown |
|-------|--------|-----------------|
| 429 | Rate limited | `Retry-After` header value, or 60s |
| 5xx | Server error | 30s |
| 401/403 | Auth failure | 300s (Phase 3 adds token refresh) |
| Timeout / connection error | Network | 15s |
| 400 / other 4xx | Client error | No fallback (surface to client) |
| 200 success | — | No fallback |

Cooldown state is in-memory: `dict[str, float]` mapping account id → expiry timestamp. Not persisted across restarts (Phase 6 adds persistence).

## 6. Architecture

### 6.1 Registry changes (`providers/registry.py`)

- `register(config)` appends to `dict[str, list[ProviderConfig]]` keyed by prefix.
- `lookup(model_str) -> list[ResolvedTarget]` — returns ALL accounts for a model (not just one).
- New `lookup_combo(name) -> list[str] | None` — returns the model list for a combo name.
- `ResolvedTarget` gains an `account_id: str` field (the `ProviderConfig.id`).

### 6.2 FallbackHandler (`routing/fallback.py`)

```python
class FallbackHandler:
    def __init__(self, registry: ProviderRegistry) -> None: ...
    
    def resolve_attempts(self, model_str: str) -> list[ResolvedTarget]:
        """Expand combo → ordered models → all available accounts per model.
        Filters out cooled-down accounts. Raises if none available."""
    
    def mark_cooldown(self, account_id: str, error_type: str, retry_after: float | None = None) -> None:
        """Mark an account as unavailable for a duration based on error type."""
    
    def is_available(self, account_id: str) -> bool:
        """True if account is past its cooldown."""
```

### 6.3 Routes integration (`api/routes.py`)

The `_handle` function becomes a retry loop:

```
attempts = handler.resolve_attempts(model_str)
for target in attempts:
    try:
        provider = build_provider(target)
        result = provider.call(upstream_payload, stream)
        if result.status_code >= 400 and is_fallback_eligible(result.status_code):
            handler.mark_cooldown(target.account_id, ...)
            continue
        return translate_and_respond(result, ...)
    except (httpx.TimeoutException, httpx.ConnectError):
        handler.mark_cooldown(target.account_id, "network")
        continue
raise HTTPException(503, "All providers exhausted")
```

Streaming adds complexity: once a stream starts successfully (200 + first chunk), fallback stops. If the stream errors mid-flight, we do NOT retry (can't replay partial output) — we surface the error.

## 7. Config Schema Changes

```python
class ComboConfig(BaseModel):
    name: str
    models: list[str]

class JanusConfig(BaseModel):
    server: ServerSettings = ...
    providers: list[ProviderConfig] = ...
    combos: list[ComboConfig] = Field(default_factory=list)
    api_keys: list[str] = ...
```

## 8. `/v1/models` Changes

The models endpoint now also lists combo names as available models:
```json
{"id": "my-coding-stack", "object": "model", "owned_by": "combo"}
```

## 9. Testing

- **Unit:** Combo expansion, multi-account lookup, cooldown state (mark/check/expiry), account filtering.
- **Integration:** End-to-end fallback — first provider returns 429, second succeeds. Multi-account rotation. Combo expansion. All-providers-exhausted → 503. Stream started → no fallback on mid-stream error.
- Tests mock httpx with `respx` (no real network).

## 10. Out of Scope

- OAuth + token refresh (Phase 3)
- Cooldown persistence across restarts (Phase 6)
- Quota tracking / budget limits (Phase 6)
- Dashboard UI for combo management (Phase 5)
