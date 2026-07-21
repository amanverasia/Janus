# 🔴 STRIX AGENT AUDIT — Janus v1.2.0

> **Date:** 2026-07-07  
> **Scope:** ~14K lines Python (60+ modules) + ~9K lines tests  
> **Focus:** Bugs, dead code, redundancy, optimization, architectural concerns, error handling, test coverage
>
> **Historical status (2026-07-21):** This audit describes Janus v1.2.0 and is preserved as a point-in-time record, not a current issue list. Major findings subsequently fixed include BUG-001 (streaming executors now propagate upstream status), BUG-002 (unknown dashboard clients fail closed), BUG-004 (budget-check failures are logged), and DEAD-001 (the unused resolver module was removed). Other entries may also have changed. Use `todo.md` for the living backlog and `ISSUES.md` for the reconciled sweep status.

---

## ⚡ Executive Summary

Janus is a well-architected local-first AI routing gateway with a clean canonical-model design. The codebase is actively maintained (Phase 8 parity with 9router largely complete), has good test coverage (93 test files), and follows sensible conventions. However, the audit identified **5 confirmed bugs** (1 high-severity), **3 dead code modules**, **5 areas of code duplication**, **4 optimization opportunities**, **4 architectural concerns**, **3 missing error handling paths**, and **4 test coverage gaps**.

The highest-priority fix is the **stream fallback being broken** — upstream 4xx/5xx stream errors silently bypass the cooldown/retry mechanism because `RawResult(status_code=200, ...)` is hardcoded in all streaming executor paths.

---

## 1. BUGS

### 🔴 BUG-001: Stream fallback silently broken — `RawResult.status_code` always 200

**File:** `src/janus/providers/openai_compat.py:48-52`
`src/janus/providers/github_copilot.py:178-182`

```python
async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
    payload = {**payload, "stream": True}
    async def line_iter() -> AsyncIterator[str]:
        async with self._client.stream("POST", url, json=payload, headers=self._headers) as r:
            async for raw_line in r.aiter_lines():
                yield raw_line
    return RawResult(status_code=200, lines=line_iter())
    #                  ^^^^^^^^^^^ always 200, even if upstream returns 429/503/etc.
```

**Impact:** In `api/routes.py::_handle()`, the check `if result.status_code >= 400` decides whether to cooldown the account and try the next target. For streaming requests, this check **never fires** because `status_code` is hardcoded to `200`. The stream proceeds with potentially error SSE lines, and the fallback handler never learns about the failure.

**Fix:** Check the actual response status before entering the stream generator:

```python
async def _call_stream(self, url, payload):
    ...
    async with self._client.stream("POST", url, ...) as r:
        if r.status_code >= 400:
            return RawResult(status_code=r.status_code, json_data=await r.aread())
        ...
```

**Severity:** 🔴 HIGH

---

### 🟡 BUG-002: `is_trusted_dashboard_client` returns `True` for `client = None`

**File:** `src/janus/api/auth.py:44-49`

```python
def is_trusted_dashboard_client(request: Request) -> bool:
    client = request.client
    if client is None:
        return True  # ← unconditionally trusted
```

**Impact:** When running behind a reverse proxy that doesn't set client info, or using certain ASGI middlewares, `request.client` can be `None`. This means **any** remote request gets unrestricted dashboard access — bypassing both API key and username/password auth.

**Fix:** Return `False` when `client is None`, or at minimum check for proxy headers (e.g., `X-Forwarded-For`) before trusting.

**Severity:** 🟡 MEDIUM (security)

---

### 🟡 BUG-003: `_prepare_key_storage` returns plaintext in record, encrypted in DB

**File:** `src/janus/storage/upstream_keys.py:31-33, 62-91`

```python
def _prepare_key_storage(key_value: str) -> tuple[str, str, str]:
    stored_value = encrypt_key_value(key_value)
    return stored_value, hash_upstream_key(key_value), mask_key(key_value)

# In create_upstream_key():
record = {
    "key_value": key_value,   # ← plaintext returned to caller
    ...
}
# stored_value (encrypted) goes to DB
```

**Impact:** Callers of `create_upstream_key()` receive a record dict with plaintext `key_value`. If this dict is passed to `update_upstream_key()` without setting `key_value` explicitly, it could re-store the key unencrypted (though `update_upstream_key` does re-encrypt `key_value` if present — partial mitigation).

**Fix:** Set `record["key_value"] = stored_value` or explicitly document that the return value contains the encrypted form.

**Severity:** 🟡 MEDIUM

---

### 🟡 BUG-004: `_check_budgets` silently swallows all exceptions

**File:** `src/janus/api/routes.py:49-51`

```python
try:
    statuses = ...
except Exception:
    pass  # ← no log, no metrics, just silently proceeds
return None
```

**Impact:** If the `usage` or `budgets` SQLite table is corrupted/missing, budget enforcement silently stops working for all requests. No alert, no log line, no metrics.

**Fix:** Add `logger.warning("Budget check failed: %s", e, exc_info=True)` in the except block.

**Severity:** 🟡 LOW (fail-open is deliberate, but should log)

---

### 🟡 BUG-005: `save_cooldown` fire-and-forget with no error handling

**File:** `src/janus/routing/fallback.py:131-140`

```python
def _persist_cooldown(self, account_id: str, expires_at: float) -> None:
    ...
    loop.create_task(save_cooldown(self.db_path, account_id, expires_at))
    # ↑ task silently fails if DB write errors
```

**Impact:** Cooldown state persists in-memory correctly but may be lost on restart if the DB write fails. The in-memory state is the primary source; persistence is best-effort by design.

**Fix:** Add a `task.add_done_callback(...)` with error logging.

**Severity:** 🟡 LOW

---

## 2. DEAD CODE

### ☠️ DEAD-001: `routing/resolver.py` — never imported

**File:** `src/janus/routing/resolver.py`

```python
def resolve(model_str: str, registry: ProviderRegistry) -> list[ResolvedTarget] | None:
    return registry.lookup(model_str)
```

**Status:** Never imported anywhere. `_handle()` calls `handler.resolve_attempts()` directly. The module exists as a 7-line thin wrapper with no callers.

**Action:** Remove the file, or document its intended future use.

---

### ☠️ DEAD-002: `list_upstream_keys()` and `list_upstream_keys_masked()` duplicate filter logic

**Files:** `src/janus/storage/upstream_keys.py:220-257`

These functions re-implement filter clause building that is already handled by `_list_filters()` used in `list_upstream_keys_page()`. `list_upstream_keys()` is only called by `check_all_upstream_keys()` in `inventory/key_checker.py`.

**Action:** Replace `list_upstream_keys()` with a call to `list_upstream_keys_page()` with `limit=None`, or refactor to share the `_list_filters()` helper.

---

### ☠️ DEAD-003: Duplicate `list_providers()` calls in dashboard routes

**File:** `src/janus/dashboard/routes.py:178-179, 580`

`providers_page()` calls `list_providers()` for `provider_count`, then `_enrich_providers()` calls it again. Same query, two round-trips.

**Action:** Pass the already-fetched provider list to `_enrich_providers()`.

---

## 3. CODE DUPLICATION & REDUNDANCY

### 🔄 DUP-001: URL validation duplicated between dashboard routes and inventory

**Dashboard:** `src/janus/dashboard/routes.py:693-718` — `api_fetch_models` has inline IP/scheme validation
**Inventory:** `src/janus/inventory/url_guard.py` — `BlockedUrlError`, `safe_fetch()`

Two separate URL safety mechanisms. `api_test_connection` has NO URL validation at all.

**Action:** Use `safe_fetch()` from `url_guard.py` in all dashboard routes that make outbound HTTP calls.

---

### 🔄 DUP-002: Provider→format mapping duplicated between `_build_provider()` and `_resolve_format()`

```python
# app.py
def _build_provider(config: ProviderConfig) -> Provider:
    if config.api_type == "opencode_free": ...
    if config.api_type == "openai_compat": ...
    ...

# api/routes.py
def _resolve_format(name: str) -> FormatAdapter:
    if name in ("opencode_free", "github_copilot"):
        name = "openai"
    return FORMATS[name]
```

Adding a new provider type requires touching both functions. The coupling is implicit.

**Action:** Define `api_type → format` mapping in a single constant or on `ProviderConfig`.

---

### 🔄 DUP-003: `_list_filters()` exists but isn't used by all listing functions

**File:** `src/janus/storage/upstream_keys.py`

Only `count_upstream_keys_filtered()` and `list_upstream_keys_page()` use `_list_filters()`. `list_upstream_keys()` builds its own clauses differently.

**Action:** Unify all upstream key listing to use `_list_filters()`.

---

### 🔄 DUP-004: Combo models parsing repeated in 4 places

```python
models = json.loads(parsed["models"]) if parsed["models"] else []
```

This exact pattern appears in `dashboard/routes.py` (combos_page, combos_partial), `dashboard/reload.py` (reload_combos, reload_providers), `routing/upstream_expand.py`, and `config/loader.py`.

**Action:** Add a `models_list` property to `ComboConfig` / `ProviderConfig` or a simple `parse_models_list(models_str) -> list[str]` helper.

---

### 🔄 DUP-005: `dict(row)` conversion pattern everywhere

Storage modules return `aiosqlite.Row` objects converted via `dict(row)` in many places. Some use list comprehensions, some don't.

**Action:** Use a shared helper like `_rows_to_dicts(rows) -> list[dict]`.

---

## 4. OPTIMIZATION OPPORTUNITIES

### ⚡ OPT-001: `get_all_settings()` called on every request

**File:** `src/janus/api/routes.py:98`

```python
settings = await get_all_settings(db_path)
```

Full `SELECT key, value FROM settings` DB round-trip on **every** API call. Settings change rarely (only via dashboard or CLI).

**Fix:** Cache in `app.state.settings` with a `last_modified` timestamp; invalidate on write. Expected savings: ~1ms per request (SQLite query latency).

---

### ⚡ OPT-002: `_enrich_providers()` does N sequential queries

**File:** `src/janus/dashboard/routes.py:580`

```python
for p in providers_raw:
    parsed["inventory_keys"] = await summarize_upstream_keys_for_inventory(db_path, inventory_id)
```

One DB query per provider (3-15 providers → 3-15 sequential queries).

**Fix:** Single `GROUP BY provider_id` query returning all inventory summaries.

---

### ⚡ OPT-003: `api_fetch_models` creates fresh `httpx.AsyncClient`

**File:** `src/janus/dashboard/routes.py:700-770`

```python
async with httpx.AsyncClient(timeout=15) as client:
    resp = await client.get(...)
```

New TCP connection + TLS handshake per model-fetch. Dashboard operation (infrequent), so low impact, but still avoidable.

**Fix:** Reuse a shared client stored on `app.state` with connection pooling.

---

### ⚡ OPT-004: `FallbackHandler._rotation_counters` unbounded growth

**File:** `src/janus/routing/fallback.py:32`

```python
self._rotation_counters: dict[str, int] = {}
```

One entry per unique model string. For a single-user gateway this is negligible, but over months of diverse model usage it accumulates.

**Fix:** Add an LRU cap or clear on provider reload.

---

## 5. ARCHITECTURAL CONCERNS

### 🏗️ ARCH-001: `_handle()` is 210 lines of monolithic logic

**File:** `src/janus/api/routes.py:67-210`

The central request handler does: format resolution → budget check → token saving → combo resolution → fallback iteration → streaming/non-streaming dispatch → usage recording → request logging. All in one function.

**Recommendation:** Decompose into a `GatewayPipeline` with composable stages:

```
parse_request → check_budgets → apply_savers → resolve_targets → attempt_loop → record_usage
```

---

### 🏗️ ARCH-002: `_build_provider()` lives in `app.py` but belongs in `providers/`

**File:** `src/janus/app.py:27-47`

The provider factory with `if/elif` chain is in the app module. Adding a new provider requires touching `app.py`.

**Recommendation:** Move to `providers/factory.py` with a registration dict:

```python
PROVIDER_TYPES = {
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    ...
}
```

This is [tracked in TODO](https://github.com/amanverasia/Janus).

---

### 🏗️ ARCH-003: Provider API keys stored in plaintext

**File:** `src/janus/storage/database.py` — `providers` table schema

Gateway provider `api_key` values (including GitHub Copilot OAuth tokens) are stored in plaintext in SQLite. Inventory upstream keys use Fernet encryption via `INVENTORY_ENCRYPTION_KEY`.

**Recommendation:** Reuse `inventory/key_encryption.py` helpers for the `providers` table. Encrypt on write, decrypt on read.

This is [tracked in TODO](https://github.com/amanverasia/Janus).

---

### 🏗️ ARCH-004: `inventory_provider_id_for_prefix` uses opaque derived mapping

**File:** `src/janus/routing/inventory_bridge.py:1-7`

```python
PREFIX_TO_INVENTORY: dict[str, str] = prefix_to_inventory_map()
```

The derivation (`google→gemini`, `dashscope→qwen`) comes from the unified catalog. The mapping is computed at module load but non-obvious to understand without reading `catalog.py`.

**Recommendation:** Add a docstring explaining the bridge purpose and the mapping source.

---

## 6. MISSING ERROR HANDLING

### ❌ ERR-001: `get_upstream_key_detail` doesn't handle decryption failures

**File:** `src/janus/storage/upstream_keys.py:211-220`

```python
return _decode_upstream_row(row) if row else None
# ↑ _decode_upstream_row calls decrypt_key_value() which can raise
```

If `decrypt_key_value()` raises (wrong encryption key, corrupted data), the dashboard crashes with a 500.

**Fix:** Catch `RuntimeError`/`InvalidToken` in `_decode_upstream_row` and return masked/redacted value.

---

### ❌ ERR-002: Non-fallback-eligible upstream errors skip request logging

**File:** `src/janus/api/routes.py:152-155`

```python
if result.status_code >= 400:
    if is_fallback_eligible(result.status_code):
        ...
    raise HTTPException(...)  # ← no request_log recorded
```

When the upstream returns a non-fallback-eligible 4xx (e.g., `400 Bad Request`), the error is raised as `HTTPException` but no `record_request_log()` call happens.

This is [tracked in TODO](https://github.com/amanverasia/Janus).

---

### ❌ ERR-003: `PricingRegistry.get()` returns `None` silently → $0.00 cost

**File:** `src/janus/pricing/calculator.py:8-9`

```python
pricing = registry.get(model)
if pricing is None:
    return 0.0  # ← no log, no metric
```

Unknown models get cost `$0.00` with no indication. Over time this silently distorts analytics.

**Fix:** Log at `DEBUG` level: `logger.debug("No pricing for model %s, cost recorded as $0.00", model)`.

---

## 7. TEST COVERAGE GAPS

| Module | Test File | Status |
|---|---|---|
| `inventory/recheck_scheduler.py` | — | ❌ ZERO coverage |
| `inventory/reclassify.py` | — | ❌ ZERO coverage |
| `routing/reload_bridge.py` | — | ❌ ZERO coverage |
| `tokensavers/headroom.py` | `test_headroom.py` | ⚠️ Light (only unit) |
| `providers/github_copilot.py` | `test_github_copilot.py` | ⚠️ Light (no integration) |
| `routing/fallback.py` | Tested through integration | ⚠️ No isolated unit tests for cooldown/rotation logic |
| `scripts/generate_model_catalog.py` | — | ❌ ZERO coverage |
| `scripts/seed_openrouter_pricing.py` | — | ❌ ZERO coverage |

---

## 8. MINOR ISSUES

### 🟢 MINOR-001: `_check_budgets` uses naive datetime (no tz)

**File:** `src/janus/api/routes.py:45-46`

```python
midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
```

Uses `datetime.now()` (no timezone). Consistent with SQLite `datetime('now')` but fragile across timezone changes.

---

### 🟢 MINOR-002: `api_test_connection` has no URL validation

**File:** `src/janus/dashboard/routes.py:820-920`

The "Test Connection" button in the dashboard lets you probe arbitrary URLs with your API key. `api_fetch_models` validates URLs for private IPs, but `api_test_connection` does not — it sends your credentials to whatever URL you enter.

**Fix:** Add identical URL validation from `api_fetch_models` or use `url_guard.safe_fetch()`.

---

### 🟢 MINOR-003: Anthropic `base_url` convention differs from OpenAI Compat

In the catalog, Anthropic's `base_url` is `https://api.anthropic.com` (API root), while OpenAI's is `https://api.openai.com/v1` (includes `/v1`). The Anthropic provider in `providers/anthropic.py` presumably appends `/v1/messages`. This implicit convention is fragile.

**Fix:** Document in `catalog.py` or standardize on root URLs across all providers.

---

### 🟢 MINOR-004: HTMX uses `alert()` for error handling

**Files:** `providers.html`, `combos.html`

Browser `alert()` calls on HTMX failures. Tracked in [`todo.md`](todo.md) — replace with inline toast notifications.

---

## 9. PRIORITIZED FIX LIST

| # | ID | Description | Severity | Effort |
|---|---|---|---|---|
| 1 | BUG-001 | Fix stream fallback status code | 🔴 HIGH | S |
| 2 | DEAD-001 | Remove `routing/resolver.py` | 🟢 LOW | XS |
| 3 | DUP-001 | Consolidate URL validation to `url_guard.safe_fetch()` | 🟡 MEDIUM | S |
| 4 | BUG-002 | Fix `is_trusted_dashboard_client` for `client=None` | 🟡 MEDIUM | XS |
| 5 | OPT-001 | Cache settings in `app.state` | 🟡 MEDIUM | M |
| 6 | ARCH-002 | Move `_build_provider()` to `providers/factory.py` | 🟡 MEDIUM | M |
| 7 | ERR-001 | Handle decryption errors in `_decode_upstream_row` | 🟡 MEDIUM | XS |
| 8 | BUG-004 | Log budget check failures | 🟡 LOW | XS |
| 9 | ARCH-001 | Refactor `_handle()` into pipeline stages | 🟡 MEDIUM | L |
| 10 | DEAD-002 | Unify upstream key listing to use `_list_filters()` | 🟡 LOW | S |
| 11 | DUP-004 | Extract `parse_models_list()` helper | 🟡 LOW | XS |
| 12 | OPT-002 | Single-query inventory summaries | 🟡 LOW | S |
| 13 | ERR-003 | Log $0.00 cost for unknown models | 🟡 LOW | XS |
| 14 | MINOR-002 | Add URL validation to `api_test_connection` | 🟡 MEDIUM | S |
| 15 | ARCH-003 | Encrypt provider API keys at rest | 🟡 MEDIUM | M |

**Effort key:** XS = <1h, S = 1-3h, M = 3-8h, L = 1-2 days

---

## 10. OVERALL ASSESSMENT

Janus is a solid, well-tested codebase with a clean architectural premise (canonical intermediate model) that it follows consistently. The test suite is extensive (93 test files, ~9K lines), and the code is readable with good naming conventions.

**Strengths:**
- Canonical model boundary (`formats/` ↔ `canonical/` ↔ `providers/`) is clean and enforced
- SQLite as single source of truth with idempotent migrations
- Good separation: storage, routing, formats, providers, dashboard
- Comprehensive dashboard with HTMX (no heavy JS framework)
- Hot-reload architecture for providers/combos/savers without restart

**Areas needing attention:**
- Stream fallback is silently broken (BUG-001 — highest priority)
- `_handle()` is too monolithic (210 lines, 10+ concerns)
- Some security gaps in dashboard auth (`client=None` trust, missing URL validation)
- Several dead/unused code paths
- Test gaps in scheduler, reclassifier, and reload bridge modules

**Grade:** B+ — Production-ready for single-user use, with a few sharp edges to file down.
