# Janus — Phase 1: Core Router (Design Spec)

**Status:** Approved
**Date:** 2026-06-24
**Author:** Janus project
**Reference:** [9router](https://github.com/decolua/9router) (feature-parity target, not a dependency)

---

## 1. What is Janus?

Janus is a local-first, single-user AI routing gateway. It sits between a
developer's coding tools (Claude Code, Codex, Cursor, Cline, …) and 40+ AI
providers, exposing OpenAI/Anthropic-compatible HTTP endpoints. Like the Roman
god it is named after, Janus has two faces: it faces the client on one side and
every provider on the other, translating formats and routing requests without
either side needing to know the other exists.

Janus does not generate tokens. It guards the threshold and decides which gate
each request goes through.

This spec covers **Phase 1 only**: the core routing gateway that proves
translation, streaming, and provider execution end-to-end.

### 1.1 Decisions locked during brainstorming

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tech stack | **Python 3.11+ / FastAPI** | Mature async + LLM/streaming ecosystem |
| Runtime model | **Local-first, single-user** | One developer on their own box; simple auth |
| Translation strategy | **Canonical intermediate model** | 2N adapters instead of N² translators; cleaner than 9router |
| P1 inbound formats | **OpenAI + Anthropic** | Covers Claude Code, Codex, Cursor, Cline |
| P1 outbound providers | **openai_compat, anthropic, gemini, opencode_free** | API-key + no-auth; OAuth deferred |
| Repo | **github.com/amanverasia/Janus** | Version-controlled from day one |

---

## 2. Roadmap Context (where Phase 1 sits)

Janus is decomposed into phases. Phase 1 is the foundation everything else
layers onto.

1. **Core Router MVP** *(this spec)* — gateway, translation, streaming, executors.
2. **Fallback & Combos** — multi-account rotation, combo sequences, rate-limit cooldown.
3. **Persistence & Auth** — SQLite store, dashboard JWT auth, API-key auth, OAuth + token refresh.
4. **Token Savers** — RTK tool-output compression, Caveman/Ponytail prompt injection, Headroom proxy.
5. **Dashboard UI** — provider/combo/usage/logs management.
6. **Quota & Usage Analytics** — token tracking, cost estimation, reset countdowns.
7. **Cloud sync, CLI config helpers, Docker, remaining 35+ providers.**

Every later phase is designed to layer onto the Phase 1 interfaces
(`canonical/`, `formats/`, `providers/`, `routing/`) without rewriting them.

---

## 3. Architecture

### 3.1 Translation strategy: canonical model

Janus normalizes every request to one internal **canonical model** (a superset
of OpenAI / Anthropic / Gemini), executes against a provider, and emits the
response in the **client's** format.

```
Client (OpenAI | Anthropic format)
  → POST /v1/chat/completions | /v1/messages
  → FormatParser   → CanonicalRequest        (parse inbound)
  → ModelResolver  ("glm/glm-4.7" → provider) (routing/resolver.py)
  → ProviderExecutor.execute (httpx upstream) (providers/*.py)
  → provider stream → CanonicalEvents         (normalize upstream)
  → FormatEmitter  → client SSE | JSON        (emit outbound)
```

Why canonical over pairwise (9router's approach) or pass-through:
- Adding a format = one parser + one emitter (2N), not a translation matrix (N²).
- Combo/fallback/token-saver logic (later phases) operates on one shape.
- `formats/` and `providers/` never touch each other — they only speak to
  `canonical/`. Adding Kiro/Cursor later = one new file in each directory.

### 3.2 Package layout

Each unit has one responsibility and is independently testable.

```
src/janus/
├── cli.py              # `janus` entrypoint (typer): serve / config
├── app.py              # FastAPI app factory + route registration
├── settings.py         # env + config loading (pydantic-settings)
├── api/
│   ├── routes.py       # /v1/chat/completions, /v1/messages, /v1/models, count_tokens
│   └── deps.py         # API-key gate dependency, request context
├── canonical/          # THE internal model — the heart of Janus
│   ├── models.py       # CanonicalRequest, Message, ContentPart, Tool…
│   └── events.py       # canonical streaming events
├── formats/            # adapters: parse-in / emit-out
│   ├── base.py         # Parser + Emitter protocols
│   ├── openai.py       # OpenAI chat-completions parse/emit + SSE chunk format
│   ├── anthropic.py    # Anthropic messages parse/emit + SSE event format
│   └── gemini.py       # Gemini generateContent parse/emit + SSE chunk format
├── providers/          # outbound executors
│   ├── base.py         # Provider protocol → CanonicalResponse | AsyncIterator[Event]
│   ├── registry.py     # lookup by model prefix ("glm/…", "anthropic/…")
│   ├── openai_compat.py# OpenAI-compatible (GLM/OpenRouter/OpenAI) via config
│   ├── anthropic.py    # native Anthropic
│   ├── gemini.py       # native Gemini
│   └── opencode_free.py# no-auth passthrough to opencode.ai/zen/v1
├── routing/
│   ├── resolver.py     # model string → provider + model + creds
│   └── fallback.py     # P1: single-model; P2: combos + multi-account
├── streaming/
│   ├── sse.py          # SSE encode/decode helpers
│   └── translator.py   # upstream → canonical → client-format stream
└── config/
    ├── schema.py       # pydantic Config (providers, keys, settings)
    └── loader.py       # ~/.janus/config.yaml + env overrides
```

---

## 4. The Canonical Model

The internal schema is a **superset** of what OpenAI/Anthropic/Gemini can
express. Phase 1 in-scope fields:

### 4.1 CanonicalRequest

| Field | Type | Notes |
|-------|------|-------|
| `model` | `str` | Raw model string as received |
| `system` | `list[SystemBlock]` | Lifted out of messages (Anthropic/Gemini style) |
| `messages` | `list[Message]` | Conversation history |
| `tools` | `list[Tool]` | Function definitions (JSON schema) |
| `tool_choice` | `ToolChoice` | auto \| none \| required \| specific |
| `max_tokens` | `int \| None` | Required by Anthropic; adapters supply sensible defaults |
| `temperature` | `float \| None` | |
| `top_p` | `float \| None` | |
| `stop` | `list[str] \| None` | Stop sequences |
| `stream` | `bool` | |

### 4.2 Message & content parts

`Message { role: user|assistant|tool, content: str | list[ContentPart] }`

(System is not a message role in the canonical model — it is the top-level
`system` field in `CanonicalRequest`. Inbound OpenAI `system`-role messages are
parsed/lifted into that field; Anthropic/Gemini top-level system maps directly.)

`ContentPart` is a discriminated union:
- `text` — `{ type: "text", text: str }`
- `image` — `{ type: "image", source: url | (base64 + media_type) }`
- `tool_use` — `{ type: "tool_use", id, name, input: dict }`
- `tool_result` — `{ type: "tool_result", tool_use_id, content: str | list[ContentPart] }`

> **Tool-calling is first-class in Phase 1.** Claude Code, Codex, and Cursor
> all depend on it; without it the gateway is useless to them. Images are
> modeled now but lower priority to wire than tool calls.

### 4.3 Canonical streaming events

Anthropic-style granular events are used as the canonical stream (they are the
most expressive); emitters convert them to any client format:

```
message_start
  → content_block_start (text | tool_use)
    → content_block_delta (text_delta | input_json_delta)  …(many)
  → content_block_stop
  → …(more blocks)
→ message_delta (stop_reason, usage)
→ message_stop
```

This lets one upstream stream feed any client format (e.g. an Anthropic upstream
→ OpenAI `delta` chunks for a Cursor client). Usage (`input_tokens`,
`output_tokens`, and cache fields) rides on `message_delta`.

For non-streaming requests, the provider/translator accumulates events into a
single `CanonicalResponse { role, content[], stop_reason, usage, model }`.

---

## 5. Format Adapters (`formats/`)

Each adapter is two pure, heavily-tested functions:
- `parse(raw: dict, *, source_format) -> CanonicalRequest`
- `Emitter` consuming canonical events → client bytes.

**Inbound format detection:** primarily by route
(`/v1/messages` → Anthropic, `/v1/chat/completions` → OpenAI); payload-shape
detection as a fallback.

**Outbound format:** determined by the provider's declared `native_format`.

Per-format quirks handled by each adapter:
- **OpenAI** — non-stream `{choices:[...]}`; stream `data: {choices:[{delta:…}]}\n\n` ending `data: [DONE]`. Tool calls as `tool_calls` deltas with `index`. System as a `system`-role message.
- **Anthropic** — non-stream `{type:"message", content:[…]}`; stream typed SSE events. Top-level `system`. `tool_use`/`tool_result` content blocks.
- **Gemini** — non-stream `generateContent` → `{candidates:[{content:{parts:[…]}}]}`; stream = SSE of same. `systemInstruction` top-level. `functionDeclarations` / `functionCall` / `functionResponse`.

Translation correctness lives entirely in these adapters + the canonical schema.

---

## 6. Provider Executors (`providers/`)

`Provider` protocol:

```python
async def execute(
    req: CanonicalRequest, creds: Credentials
) -> CanonicalResponse | AsyncIterator[CanonicalEvent]
```

Each provider declares: `id`, `prefix`, `native_format`, `base_url`, `auth`.

Phase 1 set:
- `openai_compat.py` — parametrized by `base_url` + key → covers **GLM, OpenRouter, OpenAI** from one executor.
- `anthropic.py` — native Anthropic (`api.anthropic.com`).
- `gemini.py` — native Gemini (`generativelanguage.googleapis.com`).
- `opencode_free.py` — no-auth passthrough to `opencode.ai/zen/v1` (models auto-fetched from `/v1/models`).

**Auth in Phase 1:** `bearer` (API key in `Authorization` header) and `none`
only. OAuth + token refresh are deferred to Phase 3.

A provider internally: builds the upstream native payload from the canonical
request (using the matching format emitter), calls upstream via `httpx`, and
parses the upstream's native stream/json back into canonical events (using the
matching format parser).

---

## 7. Request Lifecycle

1. Route receives raw body + client format (from route path).
2. `parse(body, client_fmt) -> CanonicalRequest`.
3. `resolver.resolve(req.model) -> (provider, model, creds)` — single model in P1.
4. `provider.execute(...)` → events stream (or full response).
5. `translator.translate(events, target=client_fmt) -> client bytes`.
6. **Stream:** `StreamingResponse(generator, media_type="text/event-stream")`.
   **Non-stream:** accumulate events → build full response → return JSON.
7. Usage extracted from `message_delta` → logged (persistence is Phase 6).

Client disconnect aborts the upstream call; end-of-stream flush + `[DONE]`
emitted for OpenAI clients.

---

## 8. Config

Local-first, file-based (dashboard is Phase 5):

- **`~/.janus/config.yaml`** — server settings + providers list + API keys.
- **Env overrides** — `JANUS_PORT`, `JANUS_HOST`, `JANUS_DATA_DIR`,
  `JANUS_REQUIRE_API_KEY`, `<PROVIDER>_API_KEY` (e.g. `GLM_API_KEY`).
- **CLI** — `janus config init` writes a template; `janus config path` prints
  the location; `janus serve` starts the server.
- **API-key gate** (optional) — `REQUIRE_API_KEY=true` + a key in config;
  clients send `Authorization: Bearer <key>`.

Example provider entry:
```yaml
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false

providers:
  - id: glm
    prefix: glm
    type: openai_compat
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: ${GLM_API_KEY}
    models: [glm-4.7, glm-4.6]
```

`${VAR}` tokens are resolved from the environment. Secret references must never
be logged.

---

## 9. Error Handling

- **Inbound validation** — pydantic validates early; invalid → `400` with a
  clear message in the client's error shape.
- **Provider HTTP errors** — re-shaped to the *client's* error format (OpenAI
  vs Anthropic error envelope).
- **Transient errors** (5xx, 429) — Phase 1: single retry then surface the
  error. Full 3-tier fallback arrives in Phase 2.
- **401/403** — Phase 1: surface an auth error. Refresh + retry arrives in
  Phase 3.
- **Translator/stream errors** — never break a live stream: wrap in try/except,
  emit a client-format error event if mid-stream, or an HTTP error if
  pre-stream. Errors from token savers (Phase 4) must fail open.

---

## 10. Testing Strategy (TDD throughout)

- **Translation-matrix unit tests** — for every `client_fmt × provider_fmt`
  pair, assert canonical → upstream payload and upstream-stream → client-chunks
  using recorded fixtures (no network). This is the correctness core and the
  primary defense against the "9router is full of bugs" problem.
- Adapter `parse`/`emit` round-trips; resolver; config loader (incl. `${VAR}`
  resolution and absence handling).
- **Integration** — end-to-end via FastAPI ASGI transport (`httpx` in-process)
  against a stub provider: streaming + non-streaming + tool calls +
  `/v1/models`.
- **Golden tests** for SSE framing.
- **Quality gates in CI:** `ruff`, `mypy`/`pyright`, coverage threshold.

---

## 11. Packaging & Python Specifics

- `pyproject.toml` + `uv` for env/lock; **Python 3.11+**.
- Runtime deps: `fastapi`, `uvicorn`, `httpx`, `pydantic` v2,
  `pydantic-settings`, `pyyaml`, `typer`.
- Dev deps: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`.
- Distribute via `pipx install janus` → `janus` command. Docker in Phase 7.

---

## 12. Phase 1 Success Criteria ("done")

1. `pipx install janus` then `janus serve` starts on a configurable host/port.
2. `janus config init` → edit `~/.janus/config.yaml` to add one API key.
3. **Claude Code** (Anthropic format) pointed at `http://localhost:<port>`
   works: streaming + non-streaming, **including tool calls**, routed to a
   configured provider.
4. **Codex/Cursor/Cline** (OpenAI format) similarly work.
5. **Cross-format routing verified:** Claude Code → OpenAI-compatible provider,
   and Codex → Anthropic provider.
6. `/v1/models` lists configured models.
7. Translation-matrix + integration tests green; `ruff` + `mypy` clean.

---

## 13. Out of Scope for Phase 1

- Combos / 3-tier fallback / multi-account rotation → **Phase 2**
- OAuth + token refresh (only `bearer`/`none` auth here) → **Phase 3**
- Token savers (RTK / Caveman / Ponytail / Headroom) → **Phase 4**
- Dashboard UI (config is file-based) → **Phase 5**
- Usage analytics persistence (P1 only logs) → **Phase 6**
- Cloud sync, extended CLI config helpers, Docker, the other ~35 providers → **Phase 7**
- Extended thinking / reasoning tokens, audio modality → later

---

## 14. Security-Sensitive Boundaries (Phase 1)

- API keys and provider secrets live in `~/.janus/config.yaml` or env; treat
  the data dir as sensitive (never log `${VAR}`-resolved secrets).
- The optional API-key gate protects the `/v1/*` surface when exposed beyond
  localhost (default off for local-first; `REQUIRE_API_KEY` to enable).
- Default bind is `127.0.0.1`; binding `0.0.0.0` is an explicit user choice.
