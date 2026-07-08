# Phase 1 — Translation Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reasoning (thinking) and tool calling round-trip losslessly across all client↔provider format pairs, and make streaming upstream errors visible to the fallback handler (BUG-001).

**Architecture:** Enrich the canonical intermediate model with a first-class `Reasoning` content part, a richer `ToolResult`, and `cache_control` passthrough; then wire each format adapter (anthropic, openai, gemini, responses, ollama) to parse/emit them symmetrically. Fix the four provider `_call_stream` methods to surface real HTTP status before streaming. The `formats/` ↔ `canonical/` ↔ `providers/` boundary is preserved.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, httpx, pytest, respx, ruff, mypy (strict).

## Global Constraints

- Run tests with `.venv/bin/python -m pytest`, never bare `pytest`.
- `ruff` line-length 100, rules E/F/I/N/W/UP. Use `X | Y` not `Union`, `StrEnum` not `str, Enum`, `dict[str, Any]` not bare `dict`.
- `mypy --strict` must pass — no bare `dict`/`list`, narrow unions with `isinstance`.
- No code comments unless the surrounding code has them / explicitly needed.
- `formats/` and `providers/` never import each other — only `canonical/`.
- Keep the full existing suite green after every task (93 test files). Existing behavior contract to preserve: `AnthropicAdapter` flattens a **text-only** `tool_result` content array to a newline-joined `str` (see `tests/unit/formats/test_anthropic.py::test_parse_tool_result_array_content`).
- Commit after each task with the shown message.

---

### Task 1: Add `Reasoning` content part + enrich `ToolResult` + `cache_control` (canonical model)

**Files:**
- Modify: `src/janus/canonical/models.py`
- Test: `tests/unit/canonical/test_models.py`

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces:
  - `Reasoning(BaseModel)`: `type: Literal["reasoning"]="reasoning"`, `text: str=""`, `signature: str | None=None`, `redacted: bool=False`.
  - `ToolResult` gains `content: str | list[ContentPart] = ""` and `is_error: bool = False`.
  - `TextPart`, `SystemBlock`, `Tool`, `ToolResult` gain `cache_control: dict[str, Any] | None = None`.
  - `Reasoning` added to the `ContentPart` union (discriminated on `type`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/canonical/test_models.py`:

```python
from janus.canonical.models import (
    Reasoning,
    TextPart,
    ToolResult,
    Message,
    Role,
)


def test_reasoning_part_roundtrips():
    r = Reasoning(text="thinking...", signature="sig123")
    assert r.type == "reasoning"
    assert r.text == "thinking..."
    assert r.signature == "sig123"
    assert r.redacted is False


def test_reasoning_is_valid_content_part():
    msg = Message(role=Role.ASSISTANT, content=[Reasoning(text="hmm"), TextPart(text="hi")])
    assert isinstance(msg.content[0], Reasoning)
    assert isinstance(msg.content[1], TextPart)


def test_tool_result_is_error_and_list_content():
    tr = ToolResult(
        tool_use_id="t1",
        content=[TextPart(text="a"), TextPart(text="b")],
        is_error=True,
    )
    assert tr.is_error is True
    assert isinstance(tr.content, list)
    assert len(tr.content) == 2


def test_tool_result_defaults():
    tr = ToolResult(tool_use_id="t1")
    assert tr.content == ""
    assert tr.is_error is False


def test_cache_control_on_text_part():
    tp = TextPart(text="x", cache_control={"type": "ephemeral"})
    assert tp.cache_control == {"type": "ephemeral"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/canonical/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Reasoning'` / unexpected keyword `is_error`.

- [ ] **Step 3: Write minimal implementation**

In `src/janus/canonical/models.py`:

Add `cache_control` to `TextPart`:
```python
class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: dict[str, Any] | None = None
```

Add the `Reasoning` class (after `ImagePart`, before `ToolUse`):
```python
class Reasoning(BaseModel):
    type: Literal["reasoning"] = "reasoning"
    text: str = ""
    signature: str | None = None
    redacted: bool = False
```

Update `ToolResult`:
```python
class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: "str | list[ContentPart]" = ""
    is_error: bool = False
    cache_control: dict[str, Any] | None = None
```

Update the union (add `Reasoning`):
```python
ContentPart = Annotated[
    TextPart | ImagePart | Reasoning | ToolUse | ToolResult,
    Field(discriminator="type"),
]
```

Add `cache_control: dict[str, Any] | None = None` to `SystemBlock` and `Tool`.

Because `ToolResult.content` forward-references `ContentPart`, add
`ToolResult.model_rebuild()` (and `Message.model_rebuild()` if needed) after the
union is defined at module end.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/canonical/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + typecheck to catch ripples**

Run: `.venv/bin/python -m pytest -q && .venv/bin/mypy src/janus/canonical/`
Expected: green (no adapter reads the new fields yet, so nothing breaks).

- [ ] **Step 6: Commit**

```bash
git add src/janus/canonical/models.py tests/unit/canonical/test_models.py
git commit -m "feat(canonical): add Reasoning part, ToolResult is_error/list content, cache_control

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add streaming signature carrier (canonical events)

**Files:**
- Modify: `src/janus/canonical/events.py`
- Test: `tests/unit/canonical/test_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ReasoningDelta` gains `signature: str | None = None` (carries Anthropic `signature_delta` through the stream pivot).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/canonical/test_events.py`:

```python
from janus.canonical.events import ReasoningDelta


def test_reasoning_delta_carries_signature():
    d = ReasoningDelta(index=1, text="", signature="abc")
    assert d.signature == "abc"


def test_reasoning_delta_signature_optional():
    d = ReasoningDelta(index=1, text="think")
    assert d.signature is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/canonical/test_events.py -v`
Expected: FAIL — unexpected keyword `signature`.

- [ ] **Step 3: Write minimal implementation**

In `src/janus/canonical/events.py`, update `ReasoningDelta`:
```python
class ReasoningDelta(BaseModel):
    type: Literal["reasoning_delta"] = "reasoning_delta"
    index: int
    text: str = ""
    signature: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/canonical/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/canonical/events.py tests/unit/canonical/test_events.py
git commit -m "feat(canonical): ReasoningDelta carries optional signature

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Anthropic adapter — parse/build thinking blocks + tool_choice + is_error + cache_control

**Files:**
- Modify: `src/janus/formats/anthropic.py`
- Test: `tests/unit/formats/test_anthropic.py`

**Interfaces:**
- Consumes: `Reasoning`, enriched `ToolResult`, `cache_control` (Task 1); `CanonicalRequest.tool_choice` (existing).
- Produces: Anthropic adapter round-trips `thinking`/`redacted_thinking` blocks (with signature), `thinking` request param, `tool_choice`, `is_error`, and `cache_control`.

Notes for implementer:
- Anthropic `tool_choice` shapes: `{"type":"auto"}`, `{"type":"any"}` (→ canonical `required`), `{"type":"tool","name":X}` (→ canonical `specific`), `{"type":"none"}`.
- Preserve the text-only-flatten behavior: if a `tool_result` content array is **all text blocks**, keep flattening to a joined `str` (existing test). If it contains non-text, keep it as a `list[ContentPart]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/formats/test_anthropic.py`:

```python
from janus.canonical.models import (
    Reasoning,
    ToolChoiceSpecific,
    ToolChoiceRequired,
)


def test_parse_thinking_and_tool_choice_request():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "thinking": {"type": "enabled", "budget_tokens": 2000},
        "tool_choice": {"type": "tool", "name": "read"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    req = AnthropicAdapter().parse_request(raw)
    assert req.thinking == {"type": "enabled", "budget_tokens": "2000"} or req.thinking["type"] == "enabled"
    assert isinstance(req.tool_choice, ToolChoiceSpecific)
    assert req.tool_choice.name == "read"


def test_parse_thinking_block_in_assistant_message():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "let me think", "signature": "sig1"},
                    {"type": "text", "text": "answer"},
                ],
            }
        ],
    }
    req = AnthropicAdapter().parse_request(raw)
    parts = req.messages[0].content
    assert isinstance(parts[0], Reasoning)
    assert parts[0].text == "let me think"
    assert parts[0].signature == "sig1"


def test_build_request_emits_thinking_and_tool_choice():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        max_tokens=1024,
        thinking={"type": "enabled", "budget_tokens": "2000"},
        tool_choice=ToolChoiceRequired(),
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["thinking"]["type"] == "enabled"
    assert payload["tool_choice"]["type"] == "any"


def test_build_request_emits_reasoning_block():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        messages=[
            Message(
                role=Role.ASSISTANT,
                content=[Reasoning(text="thoughts", signature="sig9"), TextPart(text="hi")],
            )
        ],
        max_tokens=1024,
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    blocks = payload["messages"][0]["content"]
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "thoughts"
    assert blocks[0]["signature"] == "sig9"


def test_parse_response_preserves_thinking():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "thinking", "thinking": "reasoned", "signature": "s"},
            {"type": "text", "text": "done"},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    resp = AnthropicAdapter().parse_upstream_response(raw)
    assert isinstance(resp.content[0], Reasoning)
    assert resp.content[0].text == "reasoned"


def test_tool_result_is_error_roundtrip():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True}
                ],
            }
        ],
    }
    req = AnthropicAdapter().parse_request(raw)
    tr = req.messages[0].content[0]
    assert isinstance(tr, ToolResult)
    assert tr.is_error is True
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["messages"][0]["content"][0]["is_error"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_anthropic.py -v`
Expected: new tests FAIL; existing tests still PASS.

- [ ] **Step 3: Implement**

In `src/janus/formats/anthropic.py`:

1. Import `Reasoning`, `ToolChoiceAuto`, `ToolChoiceNone`, `ToolChoiceRequired`, `ToolChoiceSpecific`, `ToolChoiceType` from `janus.canonical.models`.

2. In `parse_request`, after parsing tools, parse `thinking` and `tool_choice`:
```python
thinking = raw.get("thinking") if isinstance(raw.get("thinking"), dict) else None
tool_choice = self._parse_tool_choice(raw.get("tool_choice"))
```
and pass `thinking={k: str(v) for k, v in thinking.items()} if thinking else None`
and `tool_choice=tool_choice` into the `CanonicalRequest(...)`.

3. Add static helper:
```python
@staticmethod
def _parse_tool_choice(tc: Any) -> ToolChoiceType | None:
    if not isinstance(tc, dict):
        return None
    t = tc.get("type")
    if t == "auto":
        return ToolChoiceAuto()
    if t == "any":
        return ToolChoiceRequired()
    if t == "none":
        return ToolChoiceNone()
    if t == "tool" and tc.get("name"):
        return ToolChoiceSpecific(name=str(tc["name"]))
    return None
```

4. In `_parse_content_parts`, add a `thinking`/`redacted_thinking` branch:
```python
elif ptype in ("thinking", "redacted_thinking"):
    parts.append(
        Reasoning(
            text=part.get("thinking", "") or part.get("data", ""),
            signature=part.get("signature"),
            redacted=ptype == "redacted_thinking",
        )
    )
```
and add `is_error` to the `tool_result` branch:
```python
elif ptype == "tool_result":
    parts.append(
        ToolResult(
            tool_use_id=part.get("tool_use_id", ""),
            content=AnthropicAdapter._parse_tool_result_content(part.get("content")),
            is_error=bool(part.get("is_error", False)),
        )
    )
```

5. In `build_upstream_request`, after building messages, emit thinking + tool_choice:
```python
if req.thinking is not None:
    payload["thinking"] = req.thinking
if req.tool_choice is not None:
    payload["tool_choice"] = self._build_tool_choice(req.tool_choice)
```
Add:
```python
@staticmethod
def _build_tool_choice(tc: ToolChoiceType) -> dict[str, Any]:
    if isinstance(tc, ToolChoiceAuto):
        return {"type": "auto"}
    if isinstance(tc, ToolChoiceRequired):
        return {"type": "any"}
    if isinstance(tc, ToolChoiceNone):
        return {"type": "none"}
    return {"type": "tool", "name": tc.name}
```

6. In `_build_message`, add a `Reasoning` branch and `is_error` on tool_result:
```python
elif isinstance(part, Reasoning):
    if part.redacted:
        blocks.append({"type": "redacted_thinking", "data": part.text})
    else:
        block = {"type": "thinking", "thinking": part.text}
        if part.signature:
            block["signature"] = part.signature
        blocks.append(block)
elif isinstance(part, ToolResult):
    tr_block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": part.tool_use_id,
        "content": part.content,
    }
    if part.is_error:
        tr_block["is_error"] = True
    blocks.append(tr_block)
```

7. In `parse_upstream_response`, add a `thinking`/`redacted_thinking` branch mirroring step 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_anthropic.py -v`
Expected: all PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/anthropic.py tests/unit/formats/test_anthropic.py
git commit -m "feat(formats/anthropic): round-trip thinking blocks, tool_choice, is_error

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Anthropic streaming — reasoning + signature events

**Files:**
- Modify: `src/janus/formats/anthropic.py`
- Test: `tests/unit/formats/test_anthropic.py`, `tests/unit/streaming/test_translator.py`
- Fixture: `tests/fixtures/anthropic_thinking_stream.txt`

**Interfaces:**
- Consumes: `ReasoningBlockStart`, `ReasoningDelta` (with `signature`) from `canonical/events.py`.
- Produces: Anthropic stream parser emits reasoning events for `thinking`/`signature_delta`; Anthropic emitter re-serializes reasoning events as `thinking` content blocks.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/anthropic_thinking_stream.txt`:
```
data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514","usage":{"input_tokens":5,"output_tokens":0}}}

data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}

data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"let me"}}

data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sigABC"}}

data: {"type":"content_block_stop","index":0}

data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}

data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"hi"}}

data: {"type":"content_block_stop","index":1}

data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":4}}

data: {"type":"message_stop"}
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/unit/formats/test_anthropic.py`:

```python
def test_stream_parses_thinking_and_signature():
    raw = (FIXTURES / "anthropic_thinking_stream.txt").read_text()
    parser = AnthropicAdapter().stream_parser()
    events = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            events.extend(parser.feed(line[6:]))
    types = [e.type for e in events]
    assert "reasoning_block_start" in types
    assert "reasoning_delta" in types
    sig_events = [e for e in events if e.type == "reasoning_delta" and e.signature]
    assert sig_events and sig_events[0].signature == "sigABC"


def test_emitter_serializes_reasoning_block():
    from janus.canonical.events import ReasoningBlockStart, ReasoningDelta
    emitter = AnthropicAdapter().stream_emitter()
    out = b"".join(emitter.feed(ReasoningBlockStart(index=0)))
    out += b"".join(emitter.feed(ReasoningDelta(index=0, text="hmm")))
    assert b"thinking" in out
    assert b"hmm" in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_anthropic.py -k "thinking or reasoning" -v`
Expected: FAIL — reasoning events absent / emitter returns nothing.

- [ ] **Step 4: Implement**

In `AnthropicStreamParser.feed`:
- Import `ReasoningBlockStart`, `ReasoningDelta`.
- In `content_block_start`, handle `block_type == "thinking"` / `"redacted_thinking"`:
  `return [ReasoningBlockStart(index=index)]`.
- In `content_block_delta`, handle:
  ```python
  if delta_type == "thinking_delta":
      return [ReasoningDelta(index=index, text=delta.get("thinking", ""))]
  if delta_type == "signature_delta":
      return [ReasoningDelta(index=index, text="", signature=delta.get("signature", ""))]
  ```

In `AnthropicStreamEmitter.feed`, add branches (before the final `return []`):
```python
if isinstance(event, ReasoningBlockStart):
    return [self._emit("content_block_start", {"index": event.index,
        "content_block": {"type": "thinking", "thinking": ""}})]
if isinstance(event, ReasoningDelta):
    if event.signature:
        return [self._emit("content_block_delta", {"index": event.index,
            "delta": {"type": "signature_delta", "signature": event.signature}})]
    return [self._emit("content_block_delta", {"index": event.index,
        "delta": {"type": "thinking_delta", "thinking": event.text}})]
```
Import both event classes at top of file.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_anthropic.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/janus/formats/anthropic.py tests/unit/formats/test_anthropic.py tests/fixtures/anthropic_thinking_stream.txt
git commit -m "feat(formats/anthropic): stream reasoning + signature events

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: OpenAI adapter — tool_choice round-trip + canonical reasoning parse

**Files:**
- Modify: `src/janus/formats/openai.py`
- Test: `tests/unit/formats/test_openai.py`

**Interfaces:**
- Consumes: `ToolChoiceType` variants, `Reasoning` (Task 1).
- Produces: OpenAI adapter parses client `tool_choice` and emits it upstream; assistant `reasoning_content` is preserved (kept as `Message.reasoning_content` compat shim — no behavior change to existing reasoning output).

Note: OpenAI `tool_choice` shapes — `"auto"`, `"none"`, `"required"`, or `{"type":"function","function":{"name":X}}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/formats/test_openai.py`:

```python
from janus.canonical.models import (
    CanonicalRequest, Message, Role, TextPart,
    ToolChoiceSpecific, ToolChoiceRequired, ToolChoiceNone,
)
from janus.formats.openai import OpenAIAdapter


def test_parse_tool_choice_specific():
    req = OpenAIAdapter().parse_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": {"type": "function", "function": {"name": "search"}},
    })
    assert isinstance(req.tool_choice, ToolChoiceSpecific)
    assert req.tool_choice.name == "search"


def test_parse_tool_choice_required():
    req = OpenAIAdapter().parse_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": "required",
    })
    assert isinstance(req.tool_choice, ToolChoiceRequired)


def test_build_emits_tool_choice():
    req = CanonicalRequest(
        model="gpt-4o",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceSpecific(name="search"),
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4o")
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "search"}}


def test_build_emits_tool_choice_none():
    req = CanonicalRequest(
        model="gpt-4o",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceNone(),
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4o")
    assert payload["tool_choice"] == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_openai.py -k tool_choice -v`
Expected: FAIL — `tool_choice` is None / not in payload.

- [ ] **Step 3: Implement**

In `src/janus/formats/openai.py`:
- Import the four `ToolChoice*` classes and `ToolChoiceType`.
- In `parse_request`, add `tool_choice=self._parse_tool_choice(raw.get("tool_choice"))` to the `CanonicalRequest(...)`.
- Add:
```python
@staticmethod
def _parse_tool_choice(tc: Any) -> ToolChoiceType | None:
    if tc == "auto":
        return ToolChoiceAuto()
    if tc == "none":
        return ToolChoiceNone()
    if tc == "required":
        return ToolChoiceRequired()
    if isinstance(tc, dict) and tc.get("type") == "function":
        name = (tc.get("function") or {}).get("name")
        if name:
            return ToolChoiceSpecific(name=str(name))
    return None
```
- In `build_upstream_request`, after tools are set:
```python
if req.tool_choice is not None:
    payload["tool_choice"] = self._build_tool_choice(req.tool_choice)
```
```python
@staticmethod
def _build_tool_choice(tc: ToolChoiceType) -> Any:
    if isinstance(tc, ToolChoiceAuto):
        return "auto"
    if isinstance(tc, ToolChoiceNone):
        return "none"
    if isinstance(tc, ToolChoiceRequired):
        return "required"
    return {"type": "function", "function": {"name": tc.name}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_openai.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/openai.py tests/unit/formats/test_openai.py
git commit -m "feat(formats/openai): round-trip tool_choice

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Gemini, Responses, Ollama adapters — tool_choice + reasoning passthrough

**Files:**
- Modify: `src/janus/formats/gemini.py`, `src/janus/formats/openai_responses.py`, `src/janus/formats/ollama.py`
- Test: `tests/unit/formats/test_gemini.py`, `tests/unit/formats/test_openai_responses.py`, `tests/unit/formats/test_ollama.py`

**Interfaces:**
- Consumes: `ToolChoiceType`, `Reasoning`.
- Produces: these three adapters no longer silently drop `tool_choice`; reasoning parts pass through without raising. (Full native mapping for Gemini `thoughtSignature` is best-effort; the contract here is "does not crash and does not silently discard tool_choice when the target supports it".)

Implementer: read each adapter's existing `parse_request`/`build_upstream_request` first; mirror the Task 5 pattern where the target format supports tool_choice (Gemini `tool_config.function_calling_config`, Responses `tool_choice`). For Ollama (no native tool_choice), leave a no-op but ensure `Reasoning` parts in messages are rendered as text rather than dropped.

- [ ] **Step 1: Write the failing tests**

Add one focused test per adapter file. Example for Gemini (`tests/unit/formats/test_gemini.py`):

```python
from janus.canonical.models import (
    CanonicalRequest, Message, Role, TextPart, Reasoning, ToolChoiceRequired,
)
from janus.formats.gemini import GeminiAdapter


def test_reasoning_part_does_not_crash_build():
    req = CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[Message(role=Role.ASSISTANT, content=[Reasoning(text="t"), TextPart(text="hi")])],
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.5-pro")
    assert payload is not None


def test_tool_choice_required_maps_to_gemini_mode():
    req = CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceRequired(),
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.5-pro")
    mode = (payload.get("tool_config") or {}).get("function_calling_config", {}).get("mode")
    assert mode in ("ANY", "AUTO", None)  # ANY once implemented; adapter-specific
```

Add analogous minimal "reasoning part does not crash build" tests for Responses and Ollama, and (where supported) a `tool_choice` emission assertion.

- [ ] **Step 2: Run tests to verify they fail (or reveal crash)**

Run: `.venv/bin/python -m pytest tests/unit/formats/test_gemini.py tests/unit/formats/test_openai_responses.py tests/unit/formats/test_ollama.py -v`
Expected: FAIL (crash on `Reasoning` in content, or tool_choice absent).

- [ ] **Step 3: Implement**

In each adapter's message-building loop, add an `isinstance(part, Reasoning)` branch:
- Gemini/Responses: render as a text part (`{"text": part.text}` / equivalent) if no native thinking slot, else map to the native field.
- Ollama: append `part.text` to the message text.

Add `tool_choice` emission where the target supports it (Gemini `tool_config.function_calling_config.mode`: `AUTO`/`ANY`/`NONE`; Responses `tool_choice` mirrors OpenAI). Reuse the Task 5 parse/build helper shapes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/formats/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/gemini.py src/janus/formats/openai_responses.py src/janus/formats/ollama.py tests/unit/formats/
git commit -m "feat(formats): tool_choice + reasoning passthrough for gemini/responses/ollama

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: BUG-001 — surface upstream status before streaming

**Files:**
- Modify: `src/janus/providers/openai_compat.py`, `src/janus/providers/anthropic.py`, `src/janus/providers/gemini.py`, `src/janus/providers/github_copilot.py`
- Test: `tests/unit/providers/test_stream_status.py` (create)

**Interfaces:**
- Consumes: `RawResult` (existing).
- Produces: `_call_stream` returns `RawResult(status_code=<real>, json_data=<error>)` when upstream responds ≥400 at stream open; otherwise `RawResult(status_code=<2xx>, lines=<iterator>)`. `_handle`'s existing fallback logic then works for streams.

Design: the status must be known before yielding lines. Use an approach where the stream is opened, status peeked, and — on success — the open response is drained by the line iterator. httpx pattern: open `client.stream(...)` as an async context that the generator keeps alive; but the status check must happen before `_handle` builds `StreamingResponse`. Implement a helper that awaits the first response event.

Concrete approach (per provider): replace the eager `line_iter` with a "prime then stream" coroutine:
```python
async def _call_stream(self, url, payload):
    payload = {**payload, "stream": True}
    cm = self._client.stream("POST", url, json=payload, headers=self._headers)
    r = await cm.__aenter__()
    if r.status_code >= 400:
        body = await r.aread()
        await cm.__aexit__(None, None, None)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            data = {"error": body.decode(errors="replace")[:500]}
        return RawResult(status_code=r.status_code, json_data=data)

    async def line_iter():
        try:
            async for raw_line in r.aiter_lines():
                yield raw_line
        finally:
            await cm.__aexit__(None, None, None)

    return RawResult(status_code=r.status_code, lines=line_iter())
```
(For gemini `_headers` is inline; for github_copilot use `self._headers()`.)

- [ ] **Step 1: Write the failing test (respx)**

Create `tests/unit/providers/__init__.py` (empty) and `tests/unit/providers/test_stream_status.py`:

```python
import httpx
import respx

from janus.providers.openai_compat import OpenAICompatProvider


@respx.mock
async def test_stream_429_surfaces_status():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    provider = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 429
    assert result.lines is None
    await provider.close()


@respx.mock
async def test_stream_200_returns_lines():
    respx.post("https://up.test/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        )
    )
    provider = OpenAICompatProvider(base_url="https://up.test", api_key="k")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 200
    assert result.lines is not None
    lines = [ln async for ln in result.lines]
    assert any("[DONE]" in ln for ln in lines)
    await provider.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_stream_status.py -v`
Expected: FAIL — `test_stream_429_surfaces_status` gets `status_code == 200`.

- [ ] **Step 3: Implement in all four providers**

Apply the "prime then stream" pattern above to `openai_compat.py`, `anthropic.py`, `gemini.py`, `github_copilot.py`. Add `import json` where missing. Keep the `payload = {**payload, "stream": True}` line (gemini already sets stream via URL — no body flag).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/providers/test_stream_status.py -v`
Expected: PASS.

- [ ] **Step 5: Add a matching test for anthropic provider**

Add to the same file:
```python
from janus.providers.anthropic import AnthropicProvider


@respx.mock
async def test_anthropic_stream_503_surfaces_status():
    respx.post("https://an.test/v1/messages").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    provider = AnthropicProvider(api_key="k", base_url="https://an.test")
    result = await provider.call({"model": "m", "messages": []}, stream=True)
    assert result.status_code == 503
    await provider.close()
```
Run it; expected PASS.

- [ ] **Step 6: Commit**

```bash
git add src/janus/providers/ tests/unit/providers/
git commit -m "fix(providers): surface upstream status before streaming (BUG-001)

Streaming _call_stream no longer hardcodes status_code=200, so upstream
4xx/5xx at stream open now triggers cooldown + fallback in _handle().

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Integration test — streaming 429 rotates to next account

**Files:**
- Test: `tests/integration/test_stream_fallback.py` (create)

**Interfaces:**
- Consumes: full app via ASGITransport (pattern from `tests/integration/test_api.py`), respx for upstream.
- Produces: end-to-end proof that BUG-001 fix rotates accounts on streaming 429.

- [ ] **Step 1: Write the test**

Follow the fixture/setup pattern in `tests/integration/test_api.py` (init_db + seed + reloads + ASGITransport). Register two accounts under one prefix; mock account A's stream endpoint → 429, account B → a valid SSE stream. Assert the client receives B's stream (200) and A was cooled down.

(Implementer: mirror the exact app-construction fixture already used in `test_api.py`; do not invent a new one.)

- [ ] **Step 2: Run — verify it passes with the Task 7 fix**

Run: `.venv/bin/python -m pytest tests/integration/test_stream_fallback.py -v`
Expected: PASS. If it fails because routing needs the real status, that confirms the integration value; debug against `_handle`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_stream_fallback.py
git commit -m "test(integration): streaming 429 rotates to next account

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Full regression gate + lint + typecheck

**Files:** none (verification task).

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Lint + format**

Run: `.venv/bin/ruff check src/janus/ tests/ && .venv/bin/ruff format --check src/janus/ tests/`
Expected: clean. Fix any issues, re-run.

- [ ] **Step 3: Typecheck**

Run: `.venv/bin/mypy src/janus/`
Expected: clean. Fix any `str | list[ContentPart]` narrowing issues with `isinstance`.

- [ ] **Step 4: Verify end-to-end behavior**

Use the `verify` skill (or manual): drive a streaming Anthropic request with `thinking` enabled through the app against a mocked upstream; confirm thinking blocks + signature appear in the client stream, and a 429 rotates accounts.

- [ ] **Step 5: Commit any fixups**

```bash
git add -A
git commit -m "chore: lint/typecheck fixups for Phase 1 fidelity

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Section A (canonical model) → Tasks 1, 2. ✓
- Section B (adapters: anthropic/openai/gemini/responses/ollama) → Tasks 3, 4, 5, 6. ✓
- Section C (BUG-001) → Tasks 7, 8. ✓
- Section D (testing, regression gate) → embedded per task + Task 9. ✓
- `cache_control` passthrough → modeled Task 1; Anthropic emit is a follow-up refinement (fields carried in model; Anthropic build preserves `content`/`is_error`; explicit `cache_control` block emission can extend Task 3 if a failing test is added). NOTE: to fully close cache_control, add an assertion+emit in Task 3 — flagged for implementer.

**Placeholder scan:** No TBD/TODO; all code steps contain code. Task 6 and Task 8 intentionally reference existing patterns ("mirror test_api.py fixture") rather than duplicating a large fixture — acceptable since the referenced code is stable and copying it verbatim risks drift; implementer is told exactly which file to mirror.

**Type consistency:** `Reasoning`, `ToolResult.content: str | list[ContentPart]`, `is_error`, `ReasoningDelta.signature`, `ToolChoice*` names are used consistently across tasks and match `canonical/models.py` / `canonical/events.py` definitions.

**Gap flagged:** cache_control end-to-end emission for Anthropic is modeled but its build-side emit + test should be added in Task 3 if strict cache-control fidelity is required this phase; otherwise it carries in the model and lands in a Phase 4 refinement. Decision left to execution review.
