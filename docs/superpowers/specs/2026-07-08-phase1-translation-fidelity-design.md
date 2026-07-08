# Phase 1 — Translation Fidelity (Reasoning + Tool Calling) + BUG-001

> **Date:** 2026-07-08
> **Status:** Design — pending user review
> **Part of:** 4-phase "9router parity + audit remediation" program
> (Phase 1 = fidelity + the one HIGH stream bug; Phase 2 = robustness/pipeline;
> Phase 3 = catalog/providers; Phase 4 = hardening/cleanup)

## Problem

Real coding agents (Claude Code, Codex, Cursor, Cline, Pi, opencode) break when
routed through Janus because the **canonical translation layer loses information
that agents depend on**:

1. **Anthropic adapter drops thinking/reasoning entirely.** `parse_upstream_response`
   reads only `text` + `tool_use` blocks; `thinking`/`redacted_thinking` blocks
   vanish. `build_upstream_request` never sends `thinking`. The stream parser
   ignores `thinking_delta`/`signature_delta`. Result: Claude Code → any
   Claude-format provider loses extended thinking, and the thinking-block
   **signature** required for multi-turn tool use is stripped, corrupting tool loops.
2. **`tool_choice` is round-tripped by no adapter.** The canonical model defines
   `ToolChoiceType`, but no adapter reads it from the client request or emits it
   upstream. An agent that requires a specific tool call is silently ignored.
3. **`ToolResult.content` is `str`-only with no `is_error` flag.** Structured or
   image tool results are flattened to text; error results can't be signaled.
   (9router explicitly preserves `is_error` to protect error traces from RTK.)
4. **`cache_control` is not preserved**, so Anthropic prompt caching never engages
   through Janus — a large cost/latency regression for agent workloads.
5. **BUG-001 (STRIX 🔴 HIGH): stream fallback is silently broken.** All four
   provider `_call_stream` methods hardcode `RawResult(status_code=200, ...)`, so a
   streaming upstream 429/5xx never triggers cooldown/fallback in `_handle()`.
   This also means a rate-limited reasoning stream fails hard instead of rotating.

## Goal

Tool calling and reasoning round-trip **losslessly** across all client↔provider
format pairs, and streaming errors are visible to the fallback handler. Use
9router's canonical-pivot design as the reference (pivot reasoning + tool calls
through one intermediate representation; prefer same-format routes to avoid lossy
double-hops).

## Non-goals (deferred to later phases)

- `Retry-After` honoring, config-driven retry, connect-timeout, multi-endpoint
  `transports[]` format matching → **Phase 2**.
- `_handle()` pipeline refactor (ARCH-001) → **Phase 2** (done alongside the
  robustness rewrite of the same hot path).
- Catalog/model changes, groq id fix, cohere gateway, new providers → **Phase 3**.
- Provider-key encryption (ARCH-003), settings cache (OPT-001), dead-code and
  dup cleanups → **Phase 4**.

## Architecture constraint (unchanged)

`formats/` and `providers/` never import each other — they only talk to
`canonical/`. All work in this phase stays within `canonical/` and `formats/`
(plus the four `providers/*.py` `_call_stream` methods for BUG-001). The 2N-adapter
boundary is preserved.

---

## Section A — Enrich the canonical model (`canonical/models.py`)

The root cause is representational: adapters can't carry what the canonical model
can't express. Changes:

- **New `Reasoning` content part:**
  ```python
  class Reasoning(BaseModel):
      type: Literal["reasoning"] = "reasoning"
      text: str = ""
      signature: str | None = None      # Anthropic thinking signature / Gemini thoughtSignature
      redacted: bool = False            # Anthropic redacted_thinking
  ```
  Added to the `ContentPart` union. Reasoning becomes a **first-class content
  block** carried in `Message.content` and `CanonicalResponse.content`, not a
  side-channel string. This is the single representation Anthropic `thinking`
  blocks and OpenAI `reasoning_content` both map onto.

- **`ToolResult` enrichment:**
  ```python
  class ToolResult(BaseModel):
      type: Literal["tool_result"] = "tool_result"
      tool_use_id: str
      content: str | list[ContentPart] = ""
      is_error: bool = False
  ```

- **`cache_control` passthrough:** optional `cache_control: dict[str, Any] | None`
  on `TextPart`, `ToolResult`, `SystemBlock`, and `Tool` (Anthropic attaches it at
  these points). Emitted only by the Anthropic adapter; ignored by others.

- **`tool_choice`:** already on `CanonicalRequest` — no model change, just wire it
  through adapters (Section B).

- **Streaming reasoning signature (`canonical/events.py`):** add an optional
  `signature: str | None = None` to `ReasoningDelta` (or a small
  `ReasoningSignatureDelta` event) so Anthropic `signature_delta` survives the
  stream pivot. `ReasoningBlockStart`/`ReasoningDelta` already exist; only the
  signature carrier is missing.

- **Compat shim:** keep `Message.reasoning_content` and
  `CanonicalResponse.reasoning_content` as deprecated fields during migration.
  Adapters populate the new `Reasoning` parts; a helper bridges old↔new so nothing
  breaks mid-refactor. Removal is a Phase 4 cleanup item, not part of this phase.

**Why a content part, not a string:** multi-turn agent tool use requires the
assistant's *prior* thinking block (with signature) to be replayed back to the
provider. A single `reasoning_content: str` on the response can't represent
interleaved thinking↔tool_use ordering or carry the signature per block. 9router
models this as ordered blocks; we mirror that.

---

## Section B — Fix each adapter (`formats/`)

Each adapter implements the same six methods; changes are symmetric.

### Anthropic (`formats/anthropic.py`) — highest priority
- **`parse_request`**: parse top-level `thinking` (→ `CanonicalRequest.thinking`),
  `tool_choice` (`auto`/`any`→required/`tool`+name→specific/`none`), and in message
  content parse `thinking`/`redacted_thinking` blocks → `Reasoning` parts. Preserve
  `cache_control` on text/tool_result/system/tools.
- **`build_upstream_request`**: emit `thinking`, `tool_choice`, `Reasoning` blocks
  (as `thinking`/`redacted_thinking` with `signature`), and `cache_control`.
- **`parse_upstream_response`**: read `thinking`/`redacted_thinking` blocks →
  `Reasoning` parts (currently dropped).
- **Stream parser**: emit `ReasoningBlockStart`/`ReasoningDelta` on `thinking_delta`;
  capture `signature_delta` onto the block.
- **Stream emitter**: add handling for `ReasoningBlockStart`/`ReasoningDelta`
  (currently unhandled — falls through to `[]`) to re-serialize them as Anthropic
  `content_block_start`/`content_block_delta` of type `thinking`, plus the
  `signature_delta`. The canonical events already exist in `canonical/events.py`;
  only the Anthropic emitter branch is missing.

### OpenAI (`formats/openai.py`)
- Emit `tool_choice`: `auto`/`none`/`required`, and specific →
  `{"type":"function","function":{"name": ...}}`.
- Parse client `tool_choice` in `parse_request`.
- Replace the hardcoded `"deepseek" in model` reasoning special-casing with the
  canonical `Reasoning` part; keep the DeepSeek `reasoning_content:" "` quirk behind
  a narrow provider check (unchanged behavior, cleaner source).

### Gemini / OpenAI-Responses / Ollama
- Wire `tool_choice` and `Reasoning` parts through the same canonical
  representation. Gemini: `thinkingConfig` ↔ `CanonicalRequest.thinking`,
  `thoughtSignature` ↔ `Reasoning.signature`. Responses API: `reasoning` field ↔
  `Reasoning`. Ollama: pass reasoning text through as content (no native thinking
  block); tool_choice best-effort.

---

## Section C — BUG-001: stream fallback status (`providers/*.py`)

In `openai_compat.py`, `anthropic.py`, `gemini.py`, `github_copilot.py`
`_call_stream`: the status is currently unknowable until the generator runs.
Restructure so the response is opened and its status inspected **before**
`_handle()` commits to a `StreamingResponse`:

- Open the stream, read `response.status_code`.
- If `>= 400`: read the (small) error body and return
  `RawResult(status_code=r.status_code, json_data=<parsed error>)` — the stream is
  never handed to the client, so `_handle`'s existing
  `if result.status_code >= 400 → mark_cooldown + continue` fires.
- If OK: hand back the open line iterator as today (`status_code=r.status_code`,
  which is 200/2xx).

Implementation detail: use an `httpx` streaming context that stays open across the
status check and the subsequent `aiter_lines()`. A small helper wraps
"open → peek status → either return error result or yield lines" so all four
providers share one correct pattern rather than four copies.

---

## Section D — Testing (TDD)

Write tests first, watch them fail, then implement. New dir if needed:
`tests/unit/formats/` already exists.

**Round-trip golden tests (per fragile pair):**
- `anthropic → anthropic`: thinking block + signature + tool_use + tool_result
  preserved byte-for-byte through parse→build.
- `openai → anthropic` and `anthropic → openai`: `tool_choice` (each variant) and
  reasoning survive the pivot; tool ids preserved.
- `tool_result` with `is_error: true` and with list content preserved.
- `cache_control` present on Anthropic build output when set on input.

**Streaming tests:**
- Anthropic stream with `thinking_delta` + `signature_delta` →
  `ReasoningBlockStart`/`ReasoningDelta` canonical events → re-emitted as Anthropic
  `thinking` block; and → OpenAI `reasoning_content` deltas.
- OpenAI reasoning stream (`reasoning_content` deltas) → canonical → Anthropic
  `thinking` deltas.

**BUG-001 (respx):**
- Streaming upstream returns 429 on open → `provider.call(stream=True)` returns
  `RawResult.status_code == 429` (not 200); integration-level: `_handle` marks
  cooldown and attempts the next account.

Fixtures reuse `tests/fixtures/anthropic_stream.txt`, `openai_stream.txt`, request
JSONs. Add a `thinking` stream fixture.

**Regression gate:** full `pytest` green (existing 93 files), `ruff check`,
`ruff format --check`, `mypy --strict`.

---

## Risks & mitigations

- **Canonical model change ripples to all 5 adapters + streaming.** Mitigate with
  the compat shim (old `reasoning_content` kept) so adapters migrate one at a time
  with the suite green between each.
- **BUG-001 restructure changes streaming control flow.** Covered by a dedicated
  respx test asserting status is surfaced before the response streams.
- **`mypy --strict`** on the widened `ToolResult.content` union — handled by
  explicit `isinstance` narrowing in the build paths (pattern already used in
  `openai.py::_build_message`).

## Success criteria

- A Claude Code request with extended thinking + tool use, routed to an
  Anthropic-format provider, preserves thinking blocks, signatures, and tool loops.
- The same request routed to an OpenAI-compatible provider degrades gracefully:
  reasoning surfaces as `reasoning_content`, tool_choice is honored.
- A streaming request to a rate-limited account rotates to the next account
  instead of failing.
- Full test suite + lint + typecheck green.
