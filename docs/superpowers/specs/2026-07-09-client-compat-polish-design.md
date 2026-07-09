# Client Compatibility Polish Design

> **Date:** 2026-07-09
> **Status:** Approved — awaiting implementation plan
> **Backlog:** `todo.md` — Ollama surface completeness, request-log follow-ups, Quota UX round 2

## Goal

Close three Phase 8 follow-ups that improve client handshake reliability and operator visibility, without new product surfaces:

1. **Ollama shims** — `POST /api/show` and `POST /api/generate` so Ollama-only clients complete handshake
2. **Request logs polish** — log remaining error paths; configurable retention; dashboard pagination
3. **Quota UX round 2** — ≥80% warning banners, quota on Routing page, live provider-card refresh

## Decisions

| Topic | Choice |
|-------|--------|
| Scope | All three items in one PR (Approach A) |
| Ollama generate | Route-level prompt→chat translate + response remap; adapter stays chat-only |
| Ollama show | Static gateway metadata; validate against registry (same source as tags) |
| Tags allowlist | Filter `GET /api/tags` with `model_allowed` (parity with `/v1/models`) |
| Request-log gaps | Fix passthrough 502s via `_log_error_and_raise`; also log pre-routing client errors when logging is on |
| Fallback-eligible errors | Still not logged per attempt (only terminal 503 / non-fallback 4xx) |
| Retention | New setting `server_request_log_retention` (default `500`, clamp 50–5000) |
| Pagination | HTMX partial + Prev/Next, mirror inventory keys |
| Quota warn threshold | Fixed 80% (no per-provider `warn_pct` column) |
| Quota status | Tri-state `ok` / `warning` / `exhausted` |
| Provider poll | `GET /dashboard/api/providers/partial` every 8s (savers pattern) |
| Out of scope | Rolling 5h windows, Copilot pricing, key encryption, webhooks, routing-page live poll |

---

## 1. Ollama surface completeness

### Current state

| Route | Status |
|-------|--------|
| `POST /api/chat` | Full adapter + `_handle` |
| `GET /api/tags` | Registry listing; **no** allowlist filter |
| `GET /api/version` | Static `{"version":"0.6.0"}` (no auth) |
| `POST /api/show` | Missing |
| `POST /api/generate` | Missing |

### Shared helper

Extract `_ollama_model_entries(registry, allowed_models=None) -> list[dict]` from `ollama_tags`:

- Same entry shape as today (`name`, `model`, `modified_at`, `size`, `digest`, `details`)
- When `allowed_models` is set, keep only entries where `model_allowed(name, allowed_models)`

Use for tags listing and show lookup.

### `POST /api/show`

- Auth: `require_api_key` (same as chat/tags)
- Body: `{"name": "..."}` (also accept `"model"`)
- Lookup in `_ollama_model_entries`; if missing or not allowed → **404** with Ollama-ish `{"error": "model '…' not found"}`
- Response (static gateway stub):

```json
{
  "modelfile": "",
  "parameters": "",
  "template": "{{ .Prompt }}",
  "details": {
    "parent_model": "",
    "format": "gguf",
    "family": "janus",
    "families": ["janus"],
    "parameter_size": "N/A",
    "quantization_level": "gateway"
  },
  "model_info": {},
  "capabilities": ["completion"]
}
```

Merge `details` from the matching tags entry when present (`family` / `format` for combos).

### `POST /api/generate`

- Auth: `require_api_key`
- Body: `model`, `prompt` (required), optional `stream` (default **true**), `options`, `images`
- Translate to chat body:

```python
{
  "model": model,
  "messages": [{"role": "user", "content": prompt, ...images if any}],
  "stream": stream,
  "options": options or {},
}
```

- Call `_handle("ollama", chat_body, request)`
- Remap response:
  - Non-stream: copy chat emit fields; set `response` from `message.content`; omit `message`
  - Stream: for each NDJSON line, map `message.content` → `response`; keep `done` / `done_reason` / token counts on final chunk
- Content-Type remains `application/x-ndjson` for streams

Minimal shim: no `context`, duration fields, `suffix`, or `raw`.

### Tests

- Integration: show known model → 200; unknown → 404; allowlist blocks disallowed name
- Integration: generate non-stream + stream remap `response`
- Integration: tags filtered by key allowlist
- Unit: generate translation helper if extracted

### Docs

- `docs/api-reference.md` / `docs/client-setup.md` — document show + generate
- `todo.md` — mark Ollama surface item done
- `CHANGELOG.md` — Unreleased entry

---

## 2. Request logs polish

### 2a. Capture remaining errors

**Already logged:** success, stream finally, non-fallback upstream 4xx via `_log_error_and_raise`, final 503 exhausted.

**Fix:**

1. Passthrough stream empty-body 502s (~lines 464, 635 in `api/routes.py`) — replace bare `HTTPException(502)` with `_log_error_and_raise(...)`.
2. Pre-routing client errors when `log_requests` is on:
   - Budget exceeded (429)
   - Model not allowed (403)
   - Unknown model / bad combo (`resolve_attempts` ValueError → 400)

Add a small helper (e.g. `_log_client_error(...)`) that records `status`, truncated `error`, `request_body`, then returns/raises the same response as today. Do **not** change response shapes.

**Still not logged:** per-attempt fallback-eligible errors (429/5xx/auth/network) — only the terminal 503.

### 2b. Configurable retention

| Piece | Detail |
|-------|--------|
| Setting key | `server_request_log_retention` |
| Default | `"500"` in `SERVER_SETTING_DEFAULTS` |
| Resolver | `resolve_request_log_retention()` — int, clamp **50–5000** |
| Prune | `record_request_log(..., max_rows=...)` uses resolved value (pass from `_handle` / callers; avoid extra DB read inside storage if settings already loaded) |
| UI | Number input on Settings next to request-logging toggle; update copy that hardcodes “500” |
| CLI | Add key to `_ALLOWED_SETTING_KEYS` |

### 2c. Dashboard pagination

Mirror inventory keys:

- `_clamp_page_size(limit)` (1–200, default 100)
- `_request_logs_context(db_path, limit, offset)` using existing `list_request_logs` + `count_request_logs`
- `GET /dashboard/api/request-logs/partial?limit=&offset=`
- Prev/Next footer in `request_logs_partial.html`
- Footer text uses dynamic retention max from setting

Export remains full dump (unchanged); optional size note in UI only if needed.

### Tests

- Unit: retention clamp + prune with custom `max_rows`
- Integration: passthrough 502 logged when enabled
- Integration: budget / model_not_allowed / unknown model logged when enabled
- Integration: partial pagination (`offset` / page footer)

### Docs

- Settings / request-logs docs if they mention hardcoded 500
- `todo.md` — mark both request-log follow-ups done
- `CHANGELOG.md`

---

## 3. Quota UX round 2

### 3a. Shared status helper

In `storage/quotas.py` (or next to `_enrich_providers`):

```python
def quota_status(used: int, limit: int, warn_pct: float = 80.0) -> str:
    # "ok" | "warning" | "exhausted"
```

`_enrich_providers()` adds `status` alongside existing `percent` / `exhausted`.

### 3b. Near-exhaustion banners

On **Providers** and **Routing** pages (amber style matching routing cooldown banner):

- Show when any enabled provider has `status in ("warning", "exhausted")`
- List provider id, `used/limit metric`, `resets_in`
- Include banner in providers partial (or OOB swap) so the 8s poll keeps it fresh

### 3c. Quota on Routing page

Extend `get_routing_overview()`:

- Per gateway provider: `quota` object `{window, used, limit, metric, status, percent, resets_in, exhausted}` when configured
- Template: summary under provider header (bar or badge); account row can show `"deprioritized (quota)"` when prefix exhausted (parallel to cooldown)

Rate-limit live headroom on Routing is **out of scope** for this pass (static RPM/RPD already on inventory UI).

### 3d. Live provider-card refresh

1. `GET /dashboard/api/providers/partial` → existing `_providers_partial()`
2. On `providers.html` grid:

```html
hx-get="/dashboard/api/providers/partial"
hx-trigger="load, every 8s"
hx-swap="innerHTML"
```

Quota data continues to come from DB via `_enrich_providers` / `get_window_usage` (eventually consistent after `record_usage`).

### Tests

- Unit: `quota_status` thresholds (79 → ok, 80 → warning, 100 → exhausted)
- Integration: providers page / partial shows warning banner at ≥80%
- Integration: routing page includes quota fields for configured provider
- Integration: `GET /api/providers/partial` returns 200 with enriched cards

### Docs

- `todo.md` — mark Quota UX round 2 done
- `CHANGELOG.md`
- Brief note in architecture / dashboard docs if quota UI is documented

---

## Implementation order

1. Ollama helper + show + generate + tags filter + tests
2. Request-log error paths + retention setting + pagination + tests
3. Quota status helper + enrich + banners + routing overview + providers poll + tests
4. Docs / `todo.md` / `CHANGELOG.md`
5. Verify: `ruff`, `mypy`, `pytest`

## Error handling & fail-safe

- Ollama show/generate: same auth and `_handle` fail paths as chat
- Request logging: remain fail-open (`record_request_log` already swallows DB errors)
- Quota UI: missing/null quota fields → no bar, no banner entry (unchanged)

## Anti-goals

- No new OAuth providers, webhooks, or RBAC
- No mid-stream fallback / resume
- No true rolling 5h quota windows
- No encrypting `providers.api_key` in this PR
