# Janus — Phase 8: 9router Feature Parity (Design Spec & Plan)

**Status:** Approved
**Date:** 2026-07-05
**Reference:** [9router](https://github.com/decolua/9router) — original feature-parity target (see Phase 1 spec)

---

## 1. Goal

Close the remaining feature gaps between Janus and 9router, identified in the 2026-07-05
parity review. Janus already matches or exceeds 9router on: token savers (RTK, Caveman,
Ponytail), combos + fallback, multi-account rotation, analytics (Sankey, cost/token
toggles), tool setup helper, tool-call repair, pricing/budgets, and Docker deployment.

Remaining gaps, in implementation order:

| # | Feature | Effort | Value |
|---|---------|--------|-------|
| 8.1 | OpenAI Responses API format adapter | S | Codex CLI works natively |
| 8.2 | Request logging / debug mode | M | Troubleshooting parity |
| 8.3 | Headroom token saver integration | S | Extra input compression |
| 8.4 | OAuth provider framework + first provider | L | Free/subscription tiers — 9router's core pitch |
| 8.5 | Subscription quota tracking (reset windows) | M | Maximize subscription value (depends on 8.4) |
| 8.6 | Ollama inbound format adapter | S | Ollama-only clients |

**Anti-goals:** cloud sync (conflicts with local-first design; YAML export + DB copy is
the supported answer), Cloudflare Workers deployment (Node-only runtime), i18n READMEs.

---

## 2. Sub-phase 8.1 — OpenAI Responses API adapter (this implementation)

### Why first

Codex CLI speaks the Responses API (`/v1/responses`) natively. Today users must force
Chat Completions mode. This is the smallest gap with the highest effort-to-value ratio,
and it follows the existing six-method adapter recipe with no schema or routing changes.

### Design

New adapter `src/janus/formats/openai_responses.py` implementing the `FormatAdapter`
protocol. Registered as `"openai_responses"` in `FORMATS` (routes.py). New inbound route
`POST /v1/responses` → `_handle("openai_responses", body, request)`.

The adapter is bidirectional (all six methods), so it can also serve as an upstream
format later when the OAuth Codex provider lands (8.4).

**Request mapping (`parse_request`):**

- `instructions` → `SystemBlock`; input items with role `system`/`developer` → `SystemBlock`
- `input` as string → single user message
- `input` items: `message` → `Message` (content parts `input_text`/`output_text`/`text` →
  `TextPart`, `input_image` → `ImagePart`); `function_call` → assistant `Message` with
  `ToolUse` (arguments JSON-decoded); `function_call_output` → tool `Message` with
  `ToolResult`; `reasoning` items are skipped (opaque/encrypted from Codex)
- `tools` are flat (`{"type":"function","name",...}` — no nested `function` key)
- `max_output_tokens` → `max_tokens`; `reasoning.effort` → `reasoning_effort`
- `tool_choice` string or `{"type":"function","name"}` → canonical `ToolChoice*`
- `store`/`previous_response_id` are ignored — Janus is stateless

**Response mapping (`emit_response`):**

- `object: "response"`, `id: resp_{hex}`, `status: completed|incomplete`
  (`incomplete_details.reason: "max_output_tokens"` when stop_reason is `max_tokens`)
- Text content → one `message` output item with `output_text` parts
- `ToolUse` → `function_call` output items (`call_id`, JSON-encoded `arguments`)
- `reasoning_content` → `reasoning` output item with a `summary_text` part
- Usage: `input_tokens`/`output_tokens`/`total_tokens` + `input_tokens_details.cached_tokens`

**Streaming emitter:** named SSE events (`event:` + `data:` lines), following the
OpenAI event sequence: `response.created` → `response.in_progress` →
`response.output_item.added` / `response.content_part.added` →
`response.output_text.delta` / `response.function_call_arguments.delta` /
`response.reasoning_summary_text.delta` → corresponding `.done` events →
`response.completed` (with full accumulated response + usage). No `[DONE]` sentinel.

**Streaming parser** (for future upstream use): consumes the same event stream back
into canonical events (`response.created` → `MessageStart`, deltas → `TextDelta`/
`InputJsonDelta`, `response.completed` → `MessageDelta` + `MessageStop`).

### Testing

- Unit: request parsing (string input, message items, function_call round-trip, tools,
  system/instructions), response emission (text, tool calls, incomplete status, usage),
  emitter event sequence, upstream build.
- Integration: `POST /v1/responses` non-streaming + streaming against a respx-mocked
  OpenAI-compatible upstream (translation matrix: responses-in → chat-completions-out).

---

## 3. Sub-phase 8.2 — Request logging / debug mode

Opt-in (`settings` key, default off). Middleware/hook in `_handle()` captures request +
response bodies (truncated at ~64KB, auth headers redacted) into a `request_logs` SQLite
table with retention cap (last N=500 requests, pruned on insert). Dashboard page with
list + detail view + JSON export. Off by default — zero overhead when disabled.

## 4. Sub-phase 8.3 — Headroom token saver

New `tokensavers/headroom.py` calling an external `POST {base_url}/v1/compress` with the
request messages before other savers. Config: enabled + base URL (default
`http://localhost:8787`). Fail-open like all savers: timeout/error → original request.
Wire into `reload_savers()` in `dashboard/reload.py`.

## 5. Sub-phase 8.4 — OAuth provider framework

- `storage/oauth_tokens.py`: encrypted-at-rest token store (reuse inventory encryption),
  access/refresh tokens + expiry per provider account.
- `providers/oauth_base.py`: executor base that refreshes before expiry (single-flight
  lock), then delegates to the provider's native HTTP call.
- First provider: GitHub Copilot (device-code flow — best documented, no localhost
  callback server needed). Dashboard connect flow shows the user code + poll loop.
- Follow-ons (separate efforts): Kiro, Codex/ChatGPT subscription, Claude Code
  subscription, Vertex service-account JSON.

**Implementation note (2026-07-05):** shipped simpler than spec'd. GitHub device-flow
tokens are long-lived with no refresh token or expiry, so a dedicated `oauth_tokens`
table adds no value for Copilot — the GitHub OAuth token lives in `providers.api_key`
like any other credential, and only the *derived* short-lived Copilot session token is
refreshed (in-memory, single-flight, inside `providers/github_copilot.py`). A shared
`oauth_base.py`/token-table should be introduced when the first provider with real
refresh tokens (Codex/ChatGPT, Claude Code) lands — tracked as 8.4b in `todo.md`.

## 6. Sub-phase 8.5 — Subscription quota tracking

Per-provider quota windows (5h rolling / daily / weekly / monthly) configured per
provider type; consumption tracked from `usage` table; dashboard shows remaining quota +
reset countdown; window exhaustion feeds `resolve_attempts` ordering (same
deprioritization mechanism as RPM/RPD). Depends on 8.4 for the providers where this
matters.

## 7. Sub-phase 8.6 — Ollama inbound format adapter

`formats/ollama.py` + routes `POST /api/chat` (and `GET /api/tags` listing models like
`/v1/models`). NDJSON streaming (not SSE) — emitter returns newline-delimited JSON
chunks. Follows the standard adapter recipe otherwise.

---

## 8. Rollout

Each sub-phase ships independently: code + tests + CHANGELOG entry + docs page update
where user-facing. `todo.md` Phase 8 section tracks completion.
