# Janus — Phase 3: Token Savers (Design Spec)

**Status:** Approved
**Date:** 2026-06-24
**Builds on:** [Phase 1 Core Router](./2026-06-24-janus-phase1-core-router-design.md), [Phase 2 Fallback & Combos](./2026-06-24-janus-phase2-fallback-combos-design.md)

---

## 1. Goal

Save tokens on every request — both input (compress tool outputs before they hit the provider) and output (inject system prompts that bias the LLM toward terse, minimal responses). These savers run on the canonical request after parsing but before provider routing, so they work across all formats and providers.

## 2. Three Savers

### RTK — Tool Output Compression (−20-40% input tokens)

Tool outputs (`git diff`, `git status`, `grep`, `find`, `ls`, `tree`, log dumps…) often eat 30-50% of a prompt. RTK detects them in `tool_result` content parts and applies smart compression **before** the request reaches the provider.

- **Auto-detect:** Peek at content, match against filter patterns (git diff headers, directory listings, grep output, etc.)
- **Filters:** strip ANSI codes, collapse repeated whitespace, remove file-mode noise from diffs, deduplicate log lines, truncate at sensible boundaries.
- **Safe by design:** If a filter fails, throws, or makes output bigger — keep the original text. Errors never break the request.
- **Universal:** Runs on the canonical model before format translation, so it works across all formats.

### Caveman — Terse Output Prompt (−up to 65% output tokens)

Injects a system prompt that biases the LLM to respond terse and technical — preserving substance while cutting verbosity. Adapted from the Caveman concept.

### Ponytail — Lazy Senior Dev Prompt (fewer output tokens, less refactoring)

Injects a "lazy senior dev" system prompt — deletion over addition, stdlib over new deps, one-liners over abstractions. Three intensity levels: Lite, Full, Ultra.

## 3. Architecture

All savers operate on `CanonicalRequest` **after** `parse_request` and **before** `resolve_attempts` + `build_upstream_request`. They modify the request in-place.

```
Client → parse_request → CanonicalRequest
  → [RTK: compress tool_result content parts]
  → [Caveman: prepend system prompt]
  → [Ponytail: prepend system prompt]
  → resolve_attempts → build_upstream_request → provider
```

Savers are independent — they stack. Each one is a pure function `(CanonicalRequest) -> CanonicalRequest` (returns a modified copy). Config controls which are enabled and their settings.

### New package: `src/janus/tokensavers/`

```
src/janus/tokensavers/
├── __init__.py
├── base.py        # TokenSaver protocol: transform(req) -> CanonicalRequest
├── rtk.py         # tool output compression (filters + auto-detect)
├── caveman.py     # terse-output prompt injection
├── ponytail.py    # lazy-dev prompt injection (3 levels)
└── pipeline.py    # runs enabled savers in sequence
```

### Config additions

```yaml
token_savers:
  rtk:
    enabled: true          # default true
  caveman:
    enabled: false
  ponytail:
    enabled: false
    level: full            # lite | full | ultra
```

### Integration in routes.py `_handle()`

After `parse_request`, before `resolve_attempts`:

```python
canonical_req = client_adapter.parse_request(body)
canonical_req = saver_pipeline.apply(canonical_req)  # no-op if nothing enabled
```

## 4. RTK Filter Design

Each filter is a function `(text: str) -> str` that returns compressed text. The RTK module auto-detects which filter(s) to apply by peeking at the first ~1KB of each `tool_result` content part.

| Filter | Detection signal | What it does |
|--------|-----------------|-------------|
| `git-diff` | Lines starting with `diff --git`, `@@`, `+++`, `---` | Strip file-mode changes, collapse context, remove binary diff noise |
| `git-status` | Lines starting with ` M`, `??`, `A `, `D ` | Collapse identical status groups |
| `grep` | Pattern `file:line:content` or `file:line-content` | Deduplicate identical content lines |
| `find` / `ls` / `tree` | Indented file listings, `drwx` permissions | Collapse repeated directory structure, strip permission strings |
| `dedup-log` | Repeated timestamp-prefixed lines | Remove consecutive duplicate log entries |
| `smart-truncate` | Content > N tokens | Truncate at word boundary with `[…truncated…]` marker |
| `ansi-strip` | ANSI escape codes (`\x1b[...m`) | Strip all ANSI sequences |

All filters are applied within a try/except. If any filter raises or the result is larger than the input, the original text is kept.

## 5. Prompt Injection Design

Caveman and Ponytail both prepend a `SystemBlock` to `CanonicalRequest.system`. They do NOT replace existing system content — they add to the front. Multiple savers can stack (Caveman + Ponytail both prepend in order).

### Caveman prompt (condensed)

> Respond with maximum brevity. Technical substance must be preserved. No pleasantries, no explanations of approach, no "here's what I did." Just the answer.

### Ponytail prompts

- **Lite:** "Build what's asked. Prefer stdlib over new dependencies. Name the lazier alternative."
- **Full:** "Be a lazy senior developer. Delete > add. stdlib > new deps. One-liner > abstraction. Minimal code, minimal diff."
- **Ultra:** "YAGNI extremist. Deletion first. Ship the one-liner. Challenge unnecessary requirements in your response."

## 6. Testing

- **RTK unit tests:** Each filter with sample inputs (git diff, ls output, grep output) — assert compression + round-trip safety (original kept if result is larger).
- **RTK integration:** Canonical request with tool_result content parts → pipeline applies → assert tool_result text is compressed.
- **Caveman/Ponytail unit tests:** Request with system blocks → pipeline applies → assert system block prepended, original system blocks preserved.
- **Pipeline tests:** Multiple savers enabled → correct stacking order. All disabled → no-op.
- **Safety test:** Filter that raises → original preserved. Filter that produces larger output → original preserved.
- **Config test:** Enable/disable per saver, ponytail level parsing.

## 7. Out of Scope

- Headroom external proxy integration (later)
- Output-token measurement / savings tracking (Phase 6)
- Dashboard toggle UI (Phase 5)
- RTK filter configuration / custom filters (config-driven filter selection later)
