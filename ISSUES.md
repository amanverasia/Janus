# Janus — Codebase Sweep Issues Report

> **Date:** 2026-07-08  
> **Branch:** `main`  
> **Tests:** 492 unit passed, 18/20 integration passed

This report covers two areas: (A) implementation issues found in the full `main` sweep, and (B) provider integration gaps identified against 9router's provider registry.

> **Status update (2026-07-21):** This is a point-in-time sweep, not the living backlog. A1 is fixed: every request path starts from the post-saver canonical request (`api/routes.py`). A2 is fixed for parseable responses: passthrough paths extract and record upstream usage, while parse failures still fall back to zero. A4 is fixed as a **global** combo strategy control in Dashboard Settings; per-combo strategy remains unsupported. A3 remains open. The provider-gap section predates the current Codex, Kiro, Cursor, and Claude OAuth executors and should be read as historical; current onboarding gaps are tracked in `todo.md`. See `todo.md` for active work.

---

## A. Implementation Issues

### Issue 1 — Native Passthrough Skips Token Savers

**Severity:** 🟡 MEDIUM  
**File:** `src/janus/api/routes.py` — native passthrough block  
**Tests affected:** `test_rtk_compresses_tool_result_before_provider`, `test_usage_recorded_after_request`

**What happens:** When `client_format == target.native_format` (e.g., OpenAI client → OpenAI-compatible provider), the native passthrough block sends the raw body directly to the provider, bypassing the canonical model entirely. This means:

- `SaverPipeline.apply()` is never called → RTK, Caveman, Ponytail do not run
- `CanonicalRequest` is created but only used for model resolution and capability detection — token savers are applied to it, but the passthrough sends the original raw `body`, not the saved `canonical_req`

**Impact:** RTK compression (default ON) silently stops working for same-format requests. Users who rely on RTK to save 20-40% input tokens will not get compression when using `openai/gpt-4o` through the OpenAI-compatible endpoint, or `anthropic/claude-sonnet-4` through the Anthropic endpoint. Caveman/Ponytail prompts are also not injected.

**Root cause:** The native passthrough block was designed to skip the canonical model for performance, but it also skips the token saver pipeline which runs on `canonical_req`.

**Possible fixes:**
1. Run token savers on the raw body before passthrough (requires savers to operate on raw dict rather than CanonicalRequest)
2. Build a CanonicalRequest from the raw body after parsing, apply savers, then convert back to the provider's format via `emit_request` (if available) or `build_upstream_request`
3. Disable native passthrough when any token saver is enabled (conservative, simple)
4. Remove native passthrough entirely and only keep transport passthrough (transport passthrough is format-specific and doesn't have the same issue since it's for format-changing endpoints)

---

### Issue 2 — Native Passthrough Records Zero Tokens

**Severity:** 🟡 LOW  
**File:** `src/janus/api/routes.py` — native passthrough `record_usage` call

**What happens:** The native passthrough block calls `record_usage()` with default token counts:

```python
await record_usage(
    db_path,
    provider_id=target.provider_config.id,
    model=target.model,
    account_id=target.account_id,
    status=200,
    client_key_id=client_key_id,
    client_key_label=client_key_label,
    # input_tokens=0, output_tokens=0 (defaults)
)
```

In the normal canonical-model path, token counts are extracted from the upstream response via `parse_upstream_response()` which populates `CanonicalResponse.usage`. The passthrough path skips this — usage shows 0 input tokens and 0 output tokens for all same-format requests.

**Impact:** Usage analytics undercount tokens for same-format requests. Budget enforcement still works (it counts cost via the `cost` field, which is also 0). Spend tracking and token-based quota tracking are inaccurate.

**Root cause:** The raw response body isn't parsed for token usage. The canonical model path extracts usage through the format adapter's `parse_upstream_response()`, but passthrough sends the response directly to the client without extraction.

**Possible fixes:**
1. Parse the raw response body for usage before forwarding (provider-specific token extraction)
2. If native passthrough is removed (Issue 1 fix option 3/4), this issue disappears automatically
3. Accept the undercount as a known tradeoff of passthrough performance

---

### Issue 3 — `_passthrough_call` Accesses Private Provider Attributes

**Severity:** 🟢 MINOR  
**File:** `src/janus/api/routes.py:100-160`

**What happens:** The `_passthrough_call()` function accesses private provider internals:

```python
client = getattr(provider, "_client", None)
raw_headers = getattr(provider, "_headers", None)
```

These are undocumented internal implementation details. `_client` is the `httpx.AsyncClient` instance (present on all 5 providers). `_headers` varies:
- `OpenAICompatProvider`: `@property` returning a dict
- `AnthropicProvider`: instance attribute set in `__init__`
- `GeminiProvider`: hardcoded in `call()` method, not exposed as property
- `GitHubCopilotProvider`: method `_headers()` (callable)
- `OpenCodeFreeProvider`: inherits from `OpenAICompatProvider`

The code handles all three cases (`None`, callable, dict) but is fragile — renaming `_headers` to `_auth_headers` or removing `_client` would break passthrough silently.

**Impact:** Refactoring any provider's internals could break transport passthrough without any type errors or test failures (the passthrough code `continue`s past `None`).

**Possible fixes:**
1. Add a `passthrough_client()` method or `transport_call()` method to the `Provider` protocol
2. Build headers from `target.provider_config` instead of accessing provider internals
3. Document that `_client` and `_headers` are part of the provider's semi-public API

---

### Issue 4 — Dashboard Has No Combo Strategy UI

**Severity:** 🟢 MINOR  
**Files:** `src/janus/dashboard/routes.py`, `src/janus/dashboard/templates/settings.html`

**What happens:** The combo strategy settings (`combo_strategy`, `combo_sticky_limit`) are stored in the DB settings table and can be read/changed via:
- CLI: `janus settings set combo_strategy "round_robin"`
- Dashboard raw settings display (read-only key-value list)
- Direct SQLite

But there is no interactive dashboard UI — no dropdown, no input, no dedicated section. Compare with `server_account_strategy` and `server_sticky_limit` which have full dropdown + number input in the Settings page.

**Impact:** Users must use the CLI or raw API to configure combo rotation strategy. This is a poor UX for a feature that's otherwise fully implemented.

**Possible fix:** Add a combo strategy section to the Combos page (`/dashboard/combos`) with a per-combo strategy dropdown and sticky limit input, similar to the account strategy section on the Settings page.

---

## B. Provider Integration Gaps (vs 9router)

### Critical — Wrong api_type Would Silently Break Providers

5 9router providers use the **Claude/Anthropic format** (`format: "claude"`) but are inventory-only in Janus (no gateway entry). If gateway entries are added for them with the default `api_type: openai_compat`, Janus will POST OpenAI-format JSON to endpoints that expect Claude-format Anthropic requests. The upstream will return errors.

| Provider | 9router Format | 9router Base URL | Required Janus api_type |
|---|---|---|---|
| **minimax** | `claude` | `https://api.minimax.io/anthropic/v1/messages` | MUST be `anthropic` |
| **glm / zhipu** | `claude` | `https://api.z.ai/api/anthropic/v1/messages` | MUST be `anthropic` |
| **kimi / moonshot** | `claude` | `https://api.kimi.com/coding/v1/messages` | MUST be `anthropic` |
| **siliconflow** | `claude` (dual) | `https://api.siliconflow.com/v1/chat/completions` (openai transport) + anthropic endpoint | `openai_compat` with `transports: {"anthropic": "..."}` |
| **nvidia** | `openai` | `https://integrate.api.nvidia.com/v1/chat/completions` | `openai_compat` |

**Why this matters:** In 9router, the registry entry declares `format: "claude"` and the translator automatically serializes to Claude format. Janus has no equivalent declaration — the `api_type` field on the gateway entry IS the format declaration. If `api_type` is set wrong, the formatter serializes wrong, and the endpoint rejects the request. There are **no guardrails** to prevent this.

**Recommendation:** When adding gateway entries for providers that use Claude-format endpoints, set `api_type: "anthropic"`. Consider adding a validation step or a `format` field to `ProviderConfig` to make this explicit.

---

### Dual-Format — DeepSeek Needs transports

| Provider | 9router | Janus |
|---|---|---|
| **deepseek** | Dual transports: `openai` at `api.deepseek.com/chat/completions` + `claude` at `api.deepseek.com/anthropic/v1/messages` | Only `openai_compat` |

**Fix:** Add `transports: {"anthropic": "https://api.deepseek.com/anthropic/v1"}` to the deepseek gateway entry. This enables Janus's transport passthrough to route Anthropic-format requests to the Claude endpoint.

---

### New Provider Types Needed

| Provider | Effort | What's Needed |
|---|---|---|
| **codex** | Medium | GitHub Copilot OAuth pattern (already exists) + openai_responses format transport. Can reuse `GitHubCopilotProvider` or create a dedicated Codex provider. |
| **kiro** | High | Custom `kiro` format translator (openai↔kiro request/response). Free-tier OAuth via AWS Builder ID / Google / GitHub. Custom endpoint at `runtime.us-east-1.kiro.dev/generateAssistantResponse`. |
| **cursor** | High | Custom `cursor` format translator (openai↔cursor). Subscription OAuth. Endpoint at `api2.cursor.sh`. |
| **claude-code subscription** | High | Claude Code Pro/Max OAuth. Different from Anthropic API key. Uses Anthropic's own subscription auth flow. |

---

### Non-LLM Providers (Out of Scope)

30+ 9router providers are for image generation, STT, TTS, embeddings, or web search. Janus does not support these modalities. No action needed unless Janus expands beyond LLM routing.

---

## C. Summary

### Reconciled Status (2026-07-21)

| ID | Original finding | Current status |
|---|---|---|
| A1 | Native passthrough skips token savers | **Fixed** — all attempt paths use the post-saver canonical request. |
| A2 | Native passthrough records zero tokens | **Fixed for parseable responses** — usage is extracted through the client adapter; parse failures still fall back to zero. |
| A3 | `_passthrough_call` accesses private attrs | **Open** — transport passthrough still relies on provider `_client` and `_headers`. |
| A4 | Dashboard has no combo strategy UI | **Fixed globally** — Dashboard Settings exposes fallback, round-robin, and fusion; per-combo strategy is not supported. |
| B1 | Five providers risk a wrong wire format | **Partially superseded** — MiniMax, Zhipu/GLM, and Kimi now have gateway entries with Anthropic transports; SiliconFlow and NVIDIA remain inventory-only. |
| B2 | DeepSeek needs dual-format transports | **Fixed** — the catalog includes the Anthropic transport. |
| B3 | Codex needs a provider executor | **Fixed** — a dedicated Codex executor exists. |
| B4 | Kiro/Cursor/Claude subscription need providers | **Executors exist** — user-facing onboarding remains incomplete; see `todo.md` 8.4b. |

### Remaining action from this sweep

1. Replace `_passthrough_call`'s private provider-attribute access with an explicit provider transport interface.
2. Add onboarding flows for the newer OAuth executors as tracked in `todo.md`.
3. Decide whether SiliconFlow and NVIDIA need first-class gateway entries; if added, configure their actual wire formats explicitly.
