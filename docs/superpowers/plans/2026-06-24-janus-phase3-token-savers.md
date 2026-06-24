# Janus Phase 3: Token Savers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add RTK tool-output compression (−20-40% input tokens), Caveman terse-output prompt, and Ponytail lazy-dev prompt. All three run on the canonical request after parsing, before provider routing.

**Architecture:** New `tokensavers/` package. Each saver is a pure `transform(req) -> CanonicalRequest`. A pipeline runs enabled savers in sequence. Integrated into `_handle()` with a single line. All savers fail safe — errors never break the request.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest.

---

## File Structure

```
src/janus/tokensavers/
├── __init__.py
├── base.py        # TokenSaver protocol
├── pipeline.py    # SaverPipeline — runs enabled savers, fail-safe
├── rtk.py         # tool output compression
├── caveman.py     # terse-output prompt injection
└── ponytail.py    # lazy-dev prompt injection (3 levels)
```

Config additions in `src/janus/config/schema.py` + loader test.
Integration in `src/janus/api/routes.py` + `src/janus/app.py`.

---

### Task 1: TokenSaver base protocol + pipeline

**Files:** `src/janus/tokensavers/base.py`, `src/janus/tokensavers/pipeline.py`, `src/janus/tokensavers/__init__.py`, `tests/unit/tokensavers/test_pipeline.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/tokensavers/test_pipeline.py
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.base import TokenSaver
from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock


def test_empty_pipeline_noop():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([])
    result = pipeline.apply(req)
    assert result is req  # same object, no savers


def test_pipeline_runs_savers_in_order():
    order: list[str] = []

    class SaverA:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            order.append("a")
            return req

    class SaverB:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            order.append("b")
            return req

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([SaverA(), SaverB()])
    pipeline.apply(req)
    assert order == ["a", "b"]


def test_pipeline_saver_exception_doesnt_break():
    class BadSaver:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            raise RuntimeError("boom")

    class GoodSaver:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            req.system.append(SystemBlock(type="text", text="ok"))
            return req

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([BadSaver(), GoodSaver()])
    result = pipeline.apply(req)
    # Bad saver was skipped, good saver still ran
    assert len(result.system) == 1
    assert result.system[0].text == "ok"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/unit/tokensavers/test_pipeline.py -v
```

- [ ] **Step 3: Implement**

```python
# src/janus/tokensavers/base.py
from __future__ import annotations
from typing import Protocol
from janus.canonical.models import CanonicalRequest


class TokenSaver(Protocol):
    def transform(self, req: CanonicalRequest) -> CanonicalRequest: ...
```

```python
# src/janus/tokensavers/pipeline.py
from __future__ import annotations
import logging
from janus.canonical.models import CanonicalRequest
from .base import TokenSaver

logger = logging.getLogger(__name__)


class SaverPipeline:
    def __init__(self, savers: list[TokenSaver]) -> None:
        self._savers = savers

    def apply(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._savers:
            try:
                req = saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", type(saver).__name__, e)
        return req
```

- [ ] **Step 4: Run tests, commit**

```bash
.venv/bin/python -m pytest tests/unit/tokensavers/test_pipeline.py -v
git add src/janus/tokensavers/ tests/unit/tokensavers/ && git commit -m "feat: token saver base protocol and fail-safe pipeline"
```

---

### Task 2: RTK — tool output compression

**Files:** `src/janus/tokensavers/rtk.py`, `tests/unit/tokensavers/test_rtk.py`

This is the biggest saver. It detects tool outputs in `tool_result` content parts and compresses them.

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/tokensavers/test_rtk.py
from janus.tokensavers.rtk import RTKSaver, strip_ansi, compress_git_diff, compress_listing, dedup_lines, smart_truncate
from janus.canonical.models import CanonicalRequest, Message, Role, TextPart, ToolResult


def test_strip_ansi():
    assert strip_ansi("\x1b[32mgreen\x1b[0m text") == "green text"
    assert strip_ansi("no codes here") == "no codes here"


def test_compress_git_diff_strips_mode():
    diff = """diff --git a/foo.py b/foo.py
index 1234567..89abcde 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
-old line
+new line
 unchanged
"""
    result = compress_git_diff(diff)
    assert "index 1234567" not in result  # mode line removed
    assert "old line" in result
    assert "new line" in result


def test_compress_git_diff_bigger_returns_original():
    tiny = "diff\n"
    result = compress_git_diff(tiny)
    assert result == tiny  # if result is larger, keep original


def test_compress_listing_strips_permissions():
    listing = """drwxr-xr-x  2 user user 4096 Jun 24 src
-rw-r--r--  1 user user  100 Jun 24 main.py
-rw-r--r--  1 user user  200 Jun 24 util.py"""
    result = compress_listing(listing)
    assert "drwxr-xr-x" not in result
    assert "main.py" in result


def test_dedup_lines():
    lines = "error: failed\nerror: failed\nwarning: ok\n"
    result = dedup_lines(lines)
    assert result.count("error: failed") == 1
    assert "warning: ok" in result


def test_smart_truncate():
    long_text = "line\n" * 1000
    result = smart_truncate(long_text, max_chars=100)
    assert len(result) <= 200  # truncated + marker
    assert "truncated" in result.lower()


def test_smart_truncate_short_text_unchanged():
    result = smart_truncate("short", max_chars=100)
    assert result == "short"


def test_rtk_saver_compresses_tool_result():
    long_diff = "diff --git a/f.py b/f.py\nindex 111..222 100644\n--- a/f.py\n+++ b/f.py\n" + "line\n" * 200
    req = CanonicalRequest(
        model="m",
        messages=[
            Message(
                role=Role.TOOL,
                content=[ToolResult(type="tool_result", tool_use_id="t1", content=long_diff)],
            ),
            Message(role=Role.USER, content="fix it"),
        ],
    )
    saver = RTKSaver()
    result = saver.transform(req)
    tool_content = result.messages[0].content[0]
    assert isinstance(tool_content, ToolResult)
    assert len(tool_content.content) < len(long_diff)  # compressed


def test_rtk_saver_skips_non_tool_results():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hello")])],
    )
    saver = RTKSaver()
    result = saver.transform(req)
    assert result.messages[0].content[0].text == "hello"  # unchanged
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
# src/janus/tokensavers/rtk.py
from __future__ import annotations
import re
import logging
from janus.canonical.models import CanonicalRequest, ToolResult

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DIFF_MODE_RE = re.compile(r"^(index |old mode |new mode |similarity index |copy from |copy to |rename from |rename to |deleted file |new file mode ).*$", re.MULTILINE)
_PERMISSIONS_RE = re.compile(r"^[\s]*[dls-][rwxst-]{9}\s+(?:\d+\s+)?(?:\S+\s+)?\S+\s+\S+\s+", re.MULTILINE)
_TRUNCATE_MARKER = "\n[…truncated…]"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def compress_git_diff(text: str) -> str:
    result = _DIFF_MODE_RE.sub("", text)
    result = re.sub(r"\n{3,}", "\n\n", result)
    if len(result) >= len(text):
        return text
    return result


def compress_listing(text: str) -> str:
    result = _PERMISSIONS_RE.sub("", text)
    result = re.sub(r"\n{3,}", "\n\n", result)
    if len(result) >= len(text):
        return text
    return result


def dedup_lines(text: str) -> str:
    lines = text.split("\n")
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    output = "\n".join(result)
    if len(output) >= len(text):
        return text
    return output


def smart_truncate(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]
    return truncated + _TRUNCATE_MARKER


def _detect_and_compress(text: str) -> str:
    if not text or len(text) < 50:
        return text
    result = strip_ansi(text)
    if "diff --git" in result[:200] or result.startswith("diff "):
        result = compress_git_diff(result)
    elif re.search(r"^[dls-][rwxst-]{9}\s", result, re.MULTILINE):
        result = compress_listing(result)
    elif len(result.split("\n")) > 50 and _looks_like_log(result):
        result = dedup_lines(result)
    result = smart_truncate(result)
    return result


def _looks_like_log(text: str) -> bool:
    first_1k = text[:1024]
    log_patterns = [r"\d{4}-\d{2}-\d{2}", r"\d{2}:\d{2}:\d{2}", r"ERROR|WARN|INFO|DEBUG|TRACE"]
    return any(re.search(p, first_1k) for p in log_patterns)


class RTKSaver:
    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        for msg in req.messages:
            if not isinstance(msg.content, list):
                continue
            for i, part in enumerate(msg.content):
                if isinstance(part, ToolResult):
                    try:
                        compressed = _detect_and_compress(part.content)
                        msg.content[i] = ToolResult(
                            type="tool_result",
                            tool_use_id=part.tool_use_id,
                            content=compressed,
                        )
                    except Exception as e:
                        logger.warning("RTK compression failed: %s", e)
        return req
```

- [ ] **Step 4: Run tests, commit**

---

### Task 3: Caveman — terse output prompt

**Files:** `src/janus/tokensavers/caveman.py`, `tests/unit/tokensavers/test_caveman.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/tokensavers/test_caveman.py
from janus.tokensavers.caveman import CavemanSaver
from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock


def test_caveman_prepends_system():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = CavemanSaver()
    result = saver.transform(req)
    assert len(result.system) >= 1
    assert result.system[0].text  # non-empty prompt prepended


def test_caveman_preserves_existing_system():
    req = CanonicalRequest(
        model="m",
        system=[SystemBlock(type="text", text="You are a coder.")],
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = CavemanSaver()
    result = saver.transform(req)
    assert len(result.system) == 2
    assert result.system[0] != SystemBlock(type="text", text="You are a coder.")
    assert result.system[1].text == "You are a coder."
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
# src/janus/tokensavers/caveman.py
from __future__ import annotations
from janus.canonical.models import CanonicalRequest, SystemBlock

CAVEMAN_PROMPT = (
    "Respond with maximum brevity. Preserve technical substance. "
    "No pleasantries, no explanations of approach, no commentary. "
    "Just the answer. Why use many token when few token do trick."
)


class CavemanSaver:
    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        req.system.insert(0, SystemBlock(type="text", text=CAVEMAN_PROMPT))
        return req
```

- [ ] **Step 4: Run tests, commit**

---

### Task 4: Ponytail — lazy dev prompt (3 levels)

**Files:** `src/janus/tokensavers/ponytail.py`, `tests/unit/tokensavers/test_ponytail.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/tokensavers/test_ponytail.py
from janus.tokensavers.ponytail import PonytailSaver
from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock


def test_ponytail_lite_prepends_system():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = PonytailSaver(level="lite")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "lazy" in result.system[0].text.lower() or "stdlib" in result.system[0].text.lower()


def test_ponytail_full_level():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = PonytailSaver(level="full")
    result = saver.transform(req)
    assert len(result.system) == 1


def test_ponytail_ultra_level():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = PonytailSaver(level="ultra")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "yagni" in result.system[0].text.lower()


def test_ponytail_invalid_level_raises():
    import pytest
    with pytest.raises(ValueError, match="level"):
        PonytailSaver(level="invalid")


def test_ponytail_preserves_existing_system():
    req = CanonicalRequest(
        model="m",
        system=[SystemBlock(type="text", text="existing prompt")],
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = PonytailSaver(level="full")
    result = saver.transform(req)
    assert len(result.system) == 2
    assert result.system[1].text == "existing prompt"
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
# src/janus/tokensavers/ponytail.py
from __future__ import annotations
from janus.canonical.models import CanonicalRequest, SystemBlock

PROMPTS: dict[str, str] = {
    "lite": (
        "Build what's asked. Prefer stdlib over new dependencies. "
        "Name the lazier alternative. Minimal diff."
    ),
    "full": (
        "Be a lazy senior developer. Deletion over addition. "
        "stdlib over new deps. One-liner over abstraction. "
        "Minimal code, minimal diff. Never add code that isn't requested."
    ),
    "ultra": (
        "YAGNI extremist. Deletion first. Ship the one-liner. "
        "Challenge unnecessary requirements in your response. "
        "The best code is no code. The second best is a one-liner. "
        "stdlib > native > existing deps > one-liner > minimal code."
    ),
}


class PonytailSaver:
    def __init__(self, level: str = "full") -> None:
        if level not in PROMPTS:
            raise ValueError(f"Invalid ponytail level: {level}. Must be one of: {list(PROMPTS.keys())}")
        self.level = level

    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        req.system.insert(0, SystemBlock(type="text", text=PROMPTS[self.level]))
        return req
```

- [ ] **Step 4: Run tests, commit**

---

### Task 5: Config schema + pipeline wiring + integration

**Files:** `src/janus/config/schema.py`, `src/janus/api/routes.py`, `src/janus/app.py`, tests

- [ ] **Step 1: Add config schema**

Add to `src/janus/config/schema.py`:

```python
class TokenSaverSettings(BaseModel):
    enabled: bool = False
    level: str = "full"  # used by ponytail


class TokenSaverConfig(BaseModel):
    rtk: TokenSaverSettings = Field(default_factory=lambda: TokenSaverSettings(enabled=True))
    caveman: TokenSaverSettings = Field(default_factory=TokenSaverSettings)
    ponytail: TokenSaverSettings = Field(default_factory=TokenSaverSettings)
```

Add to `JanusConfig`:

```python
    token_savers: TokenSaverConfig = Field(default_factory=TokenSaverConfig)
```

- [ ] **Step 2: Build pipeline in app.py**

In `create_app()`, after creating the registry, build the saver pipeline:

```python
from janus.tokensavers.pipeline import SaverPipeline
from janus.tokensavers.rtk import RTKSaver
from janus.tokensavers.caveman import CavemanSaver
from janus.tokensavers.ponytail import PonytailSaver

# inside create_app, after config is set:
savers: list = []
if config.token_savers.rtk.enabled:
    savers.append(RTKSaver())
if config.token_savers.caveman.enabled:
    savers.append(CavemanSaver())
if config.token_savers.ponytail.enabled:
    savers.append(PonytailSaver(level=config.token_savers.ponytail.level))
app.state.saver_pipeline = SaverPipeline(savers)
```

- [ ] **Step 3: Integrate in routes.py `_handle()`**

After `parse_request`, before `resolve_attempts`:

```python
saver_pipeline: SaverPipeline = request.app.state.saver_pipeline
canonical_req = saver_pipeline.apply(canonical_req)
```

Add import: `from janus.tokensavers.pipeline import SaverPipeline`

- [ ] **Step 4: Write integration test**

Add to `tests/integration/test_api.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_rtk_compresses_tool_result_before_provider():
    """RTK should compress tool_result content before it reaches the provider."""
    from janus.tokensavers.pipeline import SaverPipeline
    from janus.tokensavers.rtk import RTKSaver

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="t", prefix="t", api_type="openai_compat",
                                base_url="https://fake.local/v1", api_key="k", models=["m"]))
    cfg = JanusConfig(server=ServerSettings(port=0))
    app = create_app(reg, cfg)

    long_diff = "diff --git a/f.py b/f.py\nindex 111..222 100644\n" + "line\n" * 300
    captured_payload = {}

    def capture(request):
        captured_payload.update(request.read_json())
        return httpx.Response(200, json={
            "id": "r", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    respx.post("https://fake.local/v1/chat/completions").mock(side_effect=capture)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "t/m",
            "messages": [
                {"role": "user", "content": "fix"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "diff", "arguments": "{}"}}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": long_diff},
            ],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        # The tool message content sent upstream should be compressed
        tool_msg = captured_payload["messages"][-1]
        assert len(tool_msg["content"]) < len(long_diff)
```

- [ ] **Step 5: Run all tests + lint**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: token savers (RTK compression, Caveman, Ponytail) integrated into request pipeline"
```

---

### Task 6: Full verification + push

- [ ] **Step 1: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

- [ ] **Step 2: Lint + typecheck**

```bash
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 3: Create branch and push**

```bash
git checkout -b phase3-token-savers
git push origin phase3-token-savers
gh pr create --title "feat: Phase 3 — Token Savers" --body "..."
```
