# Janus

> The two-faced gateway for AI coding tools. Janus sits at the threshold of every
> AI call — facing the developer on one side and every provider on the other.

Janus is a local-first, single-user AI routing gateway. It exposes
OpenAI/Anthropic-compatible HTTP endpoints that your coding tools (Claude Code,
Codex, Cursor, Cline, …) talk to, then translates and routes each request to
40+ AI providers — without either side needing to know the other exists.

Janus does not generate a single token. It guards the door and decides who
enters, when, and through which gate.

## Why "Janus"?

Janus is the Roman god of gateways, doorways, beginnings, and transitions —
always depicted with two faces, looking forward and backward simultaneously.
The router embodies that same duality:

- **Two faces, one system.** Janus stands between the coding tool and 40+ AI
  providers, facing the client on one side and every supported backend on the
  other. It translates formats, compresses tokens, and routes requests.
- **Keeper of passages.** Janus doesn't build the temple — he guards the door.
  The router sits at the threshold and decides where each request belongs.
- **Beginnings and transitions.** In Roman tradition, Janus was invoked at the
  start of every undertaking. Nothing reaches a model without passing through
  Janus first.
- **No allegiance, no bias.** Janus is purely Roman, standing apart. The router
  is provider-agnostic — the best gate is the one that opens to all doors.

## Status

**Phase 1 — Core Router** (in design). See the
[design spec](./docs/superpowers/specs/2026-06-24-janus-phase1-core-router-design.md).

## Tech stack

- **Runtime:** Python 3.11+
- **Framework:** FastAPI
- **HTTP client:** httpx
- **Validation:** Pydantic v2
- **Streaming:** Server-Sent Events (SSE)

## Roadmap

1. **Core Router** — gateway, format translation (canonical model), streaming, provider executors.
2. **Fallback & Combos** — multi-account rotation, 3-tier fallback (Subscription → Cheap → Free).
3. **Persistence & Auth** — SQLite store, dashboard auth, API-key auth, OAuth + token refresh.
4. **Token Savers** — RTK tool-output compression, Caveman/Ponytail prompt injection.
5. **Dashboard UI** — provider/combo/usage/logs management.
6. **Quota & Usage Analytics.**
7. **Cloud sync, CLI helpers, Docker, remaining providers.**

## License

TBD
