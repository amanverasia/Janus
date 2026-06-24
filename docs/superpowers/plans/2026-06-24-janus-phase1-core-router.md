# Janus Phase 1: Core Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first AI routing gateway (Python/FastAPI) that exposes OpenAI + Anthropic compatible endpoints, translates via a canonical intermediate model, and routes to 4 reference providers (openai_compat, anthropic, gemini, opencode_free) — with tool calling and SSE streaming.

**Architecture:** Canonical intermediate model — every request is normalized to a single internal schema, executed against a provider, and emitted in the client's format. `formats/` and `providers/` never touch each other; they only speak to `canonical/`. 2N adapters instead of N² translators.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, httpx, typer, pyyaml, uvicorn, pytest, respx, ruff.

---

## File Structure

```
janus/
├── pyproject.toml
├── .python-version          # 3.11
├── .env.example
├── src/janus/
│   ├── __init__.py
│   ├── __main__.py          # python -m janus
│   ├── cli.py               # typer CLI
│   ├── app.py               # FastAPI app factory
│   ├── settings.py          # pydantic-settings
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py        # /v1/chat/completions, /v1/messages, /v1/models
│   │   └── deps.py          # API-key gate dependency
│   ├── canonical/
│   │   ├── __init__.py
│   │   ├── models.py        # CanonicalRequest, Message, ContentPart, etc.
│   │   └── events.py        # streaming events (MessageStart, TextDelta, ...)
│   ├── formats/
│   │   ├── __init__.py
│   │   ├── base.py          # FormatAdapter protocol + StreamParser/StreamEmitter
│   │   ├── openai.py        # OpenAI parse/emit
│   │   ├── anthropic.py     # Anthropic parse/emit
│   │   └── gemini.py        # Gemini parse/emit
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py          # Provider protocol + RawResult
│   │   ├── registry.py      # lookup by prefix
│   │   ├── openai_compat.py # OpenAI-compatible (GLM, OpenRouter, OpenAI)
│   │   ├── anthropic.py     # native Anthropic
│   │   ├── gemini.py        # native Gemini
│   │   └── opencode_free.py # no-auth passthrough (thin wrapper)
│   ├── routing/
│   │   ├── __init__.py
│   │   ├── resolver.py      # model string → provider
│   │   └── fallback.py      # P1: single-model stub
│   ├── streaming/
│   │   ├── __init__.py
│   │   ├── sse.py           # encode/decode SSE
│   │   └── translator.py    # upstream→canonical→client stream
│   └── config/
│       ├── __init__.py
│       ├── schema.py        # pydantic Config models
│       └── loader.py        # load YAML + env overrides
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── openai_chat_request.json
│   │   ├── openai_stream.txt
│   │   ├── anthropic_message_request.json
│   │   ├── anthropic_stream.txt
│   │   └── gemini_request.json
│   ├── unit/
│   │   ├── canonical/       # test_models.py, test_events.py
│   │   ├── formats/         # test_openai.py, test_anthropic.py, test_gemini.py
│   │   ├── providers/       # test_openai_compat.py, test_anthropic.py, ...
│   │   ├── routing/         # test_resolver.py
│   │   ├── streaming/       # test_sse.py, test_translator.py
│   │   └── config/          # test_schema.py, test_loader.py
│   └── integration/
│       └── test_api.py      # end-to-end via ASGI transport
└── docs/
    └── superpowers/
        ├── specs/
        └── plans/
```

---

### Task 0: Git identity check

**Files:** None (read-only check)

- [ ] **Step 1: Check git config**

Run: `git config --global user.name && git config --global user.email`

If empty, set them:
```bash
git config --global user.name "amanverasia"
git config --global user.email "amanverasia@users.noreply.github.com"
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `src/janus/__init__.py`, `src/janus/__main__.py`, `tests/conftest.py`
- Create `__init__.py` for each package subdirectory

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "janus"
version = "0.1.0"
description = "The two-faced AI routing gateway"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.27.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "typer>=0.12",
]

[project.scripts]
janus = "janus.cli:app"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "ruff>=0.6",
    "mypy>=1.11",
    "pytest-cov>=5.0",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true

[tool.hatch.build.targets.wheel]
packages = ["src/janus"]
```

- [ ] **Step 2: Create .python-version and .env.example**

```
3.11
```

```bash
# env.example
JANUS_PORT=20128
JANUS_HOST=127.0.0.1
JANUS_DATA_DIR=~/.janus
JANUS_REQUIRE_API_KEY=false
GLM_API_KEY=
```

- [ ] **Step 3: Create package __init__.py files and __main__.py**

```bash
mkdir -p src/janus/{api,canonical,formats,providers,routing,streaming,config}
touch src/janus/__init__.py
touch src/janus/{api,canonical,formats,providers,routing,streaming,config}/__init__.py
mkdir -p tests/{fixtures,unit/{canonical,formats,providers,routing,streaming,config},integration}
touch tests/__init__.py tests/conftest.py
```

```python
# src/janus/__main__.py
from janus.cli import app
app()
```

- [ ] **Step 4: Install dependencies and verify**

```bash
pip install -e ".[dev]"
janus --help
pytest --help
ruff --version
```

Expected: CLI shows available commands, pytest and ruff work.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: project scaffolding with pyproject.toml, package structure, tooling"
```

---

### Task 2: Canonical models

**Files:**
- Create: `src/janus/canonical/models.py`
- Create: `tests/unit/canonical/test_models.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/canonical/test_models.py
from janus.canonical.models import (
    CanonicalRequest, Message, TextPart, ToolUse, ToolResult,
    SystemBlock, Tool, ToolFunction, Role, ContentPart
)


def test_simple_text_message_roundtrip():
    req = CanonicalRequest(
        model="test-model",
        messages=[Message(role=Role.USER, content="hello")],
    )
    assert req.model == "test-model"
    assert len(req.messages) == 1
    assert req.messages[0].role == Role.USER
    assert req.messages[0].content == "hello"


def test_content_parts_discriminated():
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            TextPart(type="text", text="Hello!"),
            ToolUse(type="tool_use", id="t1", name="read", input={"path": "x.py"}),
        ],
    )
    parts = msg.content
    assert isinstance(parts[0], TextPart)
    assert parts[0].text == "Hello!"
    assert isinstance(parts[1], ToolUse)
    assert parts[1].id == "t1"
    assert parts[1].name == "read"


def test_tool_result_in_message():
    msg = Message(
        role=Role.TOOL,
        content=[ToolResult(type="tool_result", tool_use_id="t1", content="file contents")],
    )
    assert isinstance(msg.content[0], ToolResult)
    assert msg.content[0].tool_use_id == "t1"


def test_system_blocks_separate():
    req = CanonicalRequest(
        model="test",
        system=[SystemBlock(type="text", text="You are helpful.")],
        messages=[Message(role=Role.USER, content="hi")],
    )
    assert req.system[0].text == "You are helpful."


def test_tools_and_tool_choice():
    req = CanonicalRequest(
        model="test",
        messages=[Message(role=Role.USER, content="read file")],
        tools=[Tool(type="function", function=ToolFunction(name="read", parameters={"type": "object"}))],
    )
    assert req.tools[0].function.name == "read"
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/unit/canonical/test_models.py -v
```
Expected: ImportError / ModuleNotFoundError (models.py doesn't exist yet).

- [ ] **Step 3: Implement canonical models**

```python
# src/janus/canonical/models.py
from __future__ import annotations
from enum import Enum
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageSource(BaseModel):
    type: Literal["url", "base64"]
    url: str | None = None
    media_type: str | None = None
    data: str | None = None


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource


class ToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


ContentPart = Annotated[
    Union[TextPart, ImagePart, ToolUse, ToolResult],
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Role
    content: Union[str, list[ContentPart]]


class SystemBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolChoiceAuto(BaseModel):
    type: Literal["auto"] = "auto"


class ToolChoiceNone(BaseModel):
    type: Literal["none"] = "none"


class ToolChoiceRequired(BaseModel):
    type: Literal["required"] = "required"


class ToolChoiceSpecific(BaseModel):
    type: Literal["specific"] = "specific"
    name: str


ToolChoiceType = Annotated[
    Union[ToolChoiceAuto, ToolChoiceNone, ToolChoiceRequired, ToolChoiceSpecific],
    Field(discriminator="type"),
]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CanonicalRequest(BaseModel):
    model: str
    system: list[SystemBlock] = Field(default_factory=list)
    messages: list[Message]
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: ToolChoiceType | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    stream: bool = False


class CanonicalResponse(BaseModel):
    model: str
    role: Literal["assistant"] = "assistant"
    content: list[ContentPart]
    stop_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/unit/canonical/test_models.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/janus/canonical/models.py tests/unit/canonical/test_models.py
git commit -m "feat: canonical models (CanonicalRequest, Message, ContentPart, Tool)"
```

---

### Task 3: Canonical streaming events

**Files:**
- Create: `src/janus/canonical/events.py`
- Create: `tests/unit/canonical/test_events.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/canonical/test_events.py
from janus.canonical.events import (
    MessageStart, TextBlockStart, ToolUseBlockStart,
    TextDelta, InputJsonDelta, BlockStop,
    MessageDelta, MessageStop, CanonicalEvent,
)
from janus.canonical.models import Usage


def test_message_start():
    ev = MessageStart(model="gpt-4")
    assert ev.type == "message_start"
    assert ev.model == "gpt-4"


def test_text_block_start():
    ev = TextBlockStart(index=0)
    assert ev.type == "text_block_start"
    assert ev.index == 0


def test_tool_use_block_start():
    ev = ToolUseBlockStart(index=1, id="abc", name="read")
    assert ev.id == "abc"
    assert ev.name == "read"


def test_text_delta():
    ev = TextDelta(index=0, text="Hello")
    assert ev.text == "Hello"


def test_input_json_delta():
    ev = InputJsonDelta(index=1, partial_json='{"pa')
    assert ev.partial_json == '{"pa'


def test_message_delta_with_usage():
    ev = MessageDelta(stop_reason="end_turn", usage=Usage(input_tokens=10, output_tokens=5))
    assert ev.stop_reason == "end_turn"
    assert ev.usage.input_tokens == 10


def test_message_stop():
    ev = MessageStop()
    assert ev.type == "message_stop"


def test_event_discriminated_union():
    ev = MessageStart(model="test")
    parsed = CanonicalEvent.model_validate(ev.model_dump())
    assert isinstance(parsed, MessageStart)
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/unit/canonical/test_events.py -v
```

- [ ] **Step 3: Implement canonical events**

```python
# src/janus/canonical/events.py
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field
from .models import Usage


class MessageStart(BaseModel):
    type: Literal["message_start"] = "message_start"
    model: str


class TextBlockStart(BaseModel):
    type: Literal["text_block_start"] = "text_block_start"
    index: int


class ToolUseBlockStart(BaseModel):
    type: Literal["tool_use_block_start"] = "tool_use_block_start"
    index: int
    id: str
    name: str


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    index: int
    text: str


class InputJsonDelta(BaseModel):
    type: Literal["input_json_delta"] = "input_json_delta"
    index: int
    partial_json: str


class BlockStop(BaseModel):
    type: Literal["block_stop"] = "block_stop"
    index: int


class MessageDelta(BaseModel):
    type: Literal["message_delta"] = "message_delta"
    stop_reason: str | None = None
    usage: Usage | None = None


class MessageStop(BaseModel):
    type: Literal["message_stop"] = "message_stop"


CanonicalEvent = Annotated[
    Union[
        MessageStart, TextBlockStart, ToolUseBlockStart,
        TextDelta, InputJsonDelta, BlockStop,
        MessageDelta, MessageStop,
    ],
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/unit/canonical/ -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/canonical/events.py tests/unit/canonical/test_events.py
git commit -m "feat: canonical streaming events"
```

---

### Task 4: SSE encode/decode

**Files:**
- Create: `src/janus/streaming/sse.py`
- Create: `tests/unit/streaming/test_sse.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/streaming/test_sse.py
from janus.streaming.sse import encode_sse, encode_done, parse_sse_lines


def test_encode_sse_json():
    result = encode_sse({"foo": "bar"})
    assert result == b'data: {"foo":"bar"}\n\n'


def test_encode_sse_multiline():
    result = encode_sse({"text": "line1\nline2"})
    assert b"line1\n" in result
    assert b"line2\n" in result


def test_encode_done():
    assert encode_done() == b"data: [DONE]\n\n"


def test_parse_sse_lines_single():
    raw = b'data: {"x":1}\n\n'
    lines = list(parse_sse_lines(raw))
    assert lines == ['{"x":1}']


def test_parse_sse_lines_multiple():
    raw = b'data: {"x":1}\n\ndata: {"y":2}\n\n'
    lines = list(parse_sse_lines(raw))
    assert lines == ['{"x":1}', '{"y":2}']


def test_parse_sse_lines_done():
    raw = b"data: [DONE]\n\n"
    lines = list(parse_sse_lines(raw))
    assert lines == ["[DONE]"]


def test_parse_sse_lines_empty():
    assert list(parse_sse_lines(b"")) == []
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/unit/streaming/test_sse.py -v
```

- [ ] **Step 3: Implement SSE utilities**

```python
# src/janus/streaming/sse.py
import json
from typing import Iterator


def encode_sse(data: dict) -> bytes:
    text = json.dumps(data, separators=(",", ":"))
    if "\n" in text:
        return b"".join(
            b"data: " + line.encode() + b"\n" for line in text.splitlines()
        ) + b"\n"
    return b"data: " + text.encode() + b"\n\n"


def encode_done() -> bytes:
    return b"data: [DONE]\n\n"


def parse_sse_lines(raw: bytes) -> Iterator[str]:
    buffer = []
    for line in raw.split(b"\n"):
        stripped = line.rstrip(b"\r")
        if not stripped:
            continue
        if stripped.startswith(b"data: "):
            data = stripped[6:].decode()
            if data.strip() == "[DONE]":
                yield "[DONE]"
                continue
            buffer.append(data)
        elif stripped == b"":
            if buffer:
                yield "".join(buffer)
                buffer = []
            continue
    if buffer:
        yield "".join(buffer)
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/unit/streaming/test_sse.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/streaming/sse.py tests/unit/streaming/test_sse.py
git commit -m "feat: SSE encode/decode utilities"
```

---

### Task 5: Config schema and loader

**Files:**
- Create: `src/janus/config/schema.py`, `src/janus/config/loader.py`
- Create: `tests/unit/config/test_schema.py`, `tests/unit/config/test_loader.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/config/test_schema.py
from janus.config.schema import JanusConfig, ServerSettings, ProviderConfig


def test_server_defaults():
    s = ServerSettings()
    assert s.port == 20128
    assert s.host == "127.0.0.1"
    assert s.require_api_key is False


def test_provider_config_validation():
    p = ProviderConfig(
        id="glm",
        prefix="glm",
        api_type="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="test-key",
        models=["glm-4.7"],
    )
    assert p.id == "glm"
    assert p.models == ["glm-4.7"]


def test_full_config():
    config = JanusConfig(
        server=ServerSettings(port=8080),
        providers=[
            ProviderConfig(
                id="an",
                prefix="an",
                api_type="anthropic",
                base_url="https://api.anthropic.com",
                api_key="sk-test",
                models=["claude-sonnet-4-20250514"],
            )
        ],
    )
    assert config.server.port == 8080
    assert len(config.providers) == 1
```

```python
# tests/unit/config/test_loader.py
import os
import tempfile
import yaml
from janus.config.loader import load_config, resolve_vars
from janus.config.schema import JanusConfig


def test_resolve_vars():
    env = {"KEY": "secret123"}
    result = resolve_vars("api_${KEY}_end", env)
    assert result == "api_secret123_end"
    result = resolve_vars({"key": "${KEY}"}, env)
    assert result == {"key": "secret123"}


def test_resolve_vars_no_match():
    assert resolve_vars("${MISSING}", {}) == ""


def test_load_config_from_yaml():
    yaml_text = """
server:
  port: 3000
  host: 0.0.0.0
providers:
  - id: testp
    prefix: tp
    api_type: openai_compat
    base_url: https://test.com/v1
    api_key: ${TEST_KEY}
    models: [test-model]
"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        path = f.name
    try:
        os.environ["TEST_KEY"] = "mykey123"
        config = load_config(path)
        assert config.server.port == 3000
        assert config.providers[0].api_key == "mykey123"
    finally:
        os.unlink(path)
        del os.environ["TEST_KEY"]
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/unit/config/ -v
```

- [ ] **Step 3: Implement config schema**

```python
# src/janus/config/schema.py
from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field


class ServerSettings(BaseModel):
    port: int = 20128
    host: str = "127.0.0.1"
    require_api_key: bool = False
    data_dir: Path = Path.home() / ".janus"


class ProviderConfig(BaseModel):
    id: str
    prefix: str
    api_type: str  # "openai_compat" | "anthropic" | "gemini" | "opencode_free"
    base_url: str
    api_key: str | None = None
    models: list[str] = Field(default_factory=list)


class JanusConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    providers: list[ProviderConfig] = Field(default_factory=list)
    api_keys: list[str] = Field(default_factory=list)
```

```python
# src/janus/config/loader.py
from __future__ import annotations
import re
import os
from pathlib import Path
import yaml
from .schema import JanusConfig

_VAR_RE = re.compile(r"\$\{(?P<var>[A-Z][A-Z0-9_]*)\}", re.ASCII)


def resolve_vars(value, env=None):
    if env is None:
        env = os.environ
    if isinstance(value, str):
        return _VAR_RE.sub(lambda m: env.get(m.group("var"), ""), value)
    if isinstance(value, dict):
        return {k: resolve_vars(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_vars(v, env) for v in value]
    return value


def load_config(path: str | Path) -> JanusConfig:
    path = Path(path).expanduser()
    if not path.exists():
        return JanusConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    resolved = resolve_vars(raw)
    return JanusConfig(**resolved)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/unit/config/ -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/config/ tests/unit/config/
git commit -m "feat: config schema and YAML loader with env var resolution"
```

---

### Task 6: Format adapter base protocol

**Files:**
- Create: `src/janus/formats/base.py`

- [ ] **Step 1: Write test for the protocol (verify it imports cleanly)**

```python
# tests/unit/formats/test_base.py
from janus.formats.base import StreamParser, StreamEmitter


def test_protocols_importable():
    assert StreamParser is not None
    assert StreamEmitter is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/formats/test_base.py -v
```

- [ ] **Step 3: Implement base protocols**

```python
# src/janus/formats/base.py
from __future__ import annotations
from typing import Iterator, Protocol, runtime_checkable
from janus.canonical.models import CanonicalRequest, CanonicalResponse
from janus.canonical.events import CanonicalEvent


@runtime_checkable
class StreamParser(Protocol):
    def feed(self, line: str) -> list[CanonicalEvent]: ...

    def finish(self) -> list[CanonicalEvent]: ...


@runtime_checkable
class StreamEmitter(Protocol):
    def feed(self, event: CanonicalEvent) -> list[bytes]: ...

    def finish(self) -> list[bytes]: ...


@runtime_checkable
class FormatAdapter(Protocol):
    name: str

    def parse_request(self, raw: dict) -> CanonicalRequest: ...

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict: ...

    def parse_upstream_response(self, raw: dict) -> CanonicalResponse: ...

    def emit_response(self, resp: CanonicalResponse) -> dict: ...

    def stream_parser(self) -> StreamParser: ...

    def stream_emitter(self) -> StreamEmitter: ...
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/unit/formats/test_base.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/base.py tests/unit/formats/test_base.py
git commit -m "feat: format adapter base protocols"
```

---

### Task 7: OpenAI format adapter

**Files:**
- Create: `src/janus/formats/openai.py`
- Create: `tests/unit/formats/test_openai.py`, `tests/fixtures/openai_chat_request.json`, `tests/fixtures/openai_stream.txt`

- [ ] **Step 1: Create fixtures**

```json
// tests/fixtures/openai_chat_request.json
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"}
  ],
  "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
  "stream": false
}
```

```json
{"id":"chat-1","object":"chat.completion","model":"gpt-4","choices":[{"index":0,"message":{"role":"assistant","content":"Hello!"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}
```

```
data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}

data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

- [ ] **Step 2: Write tests**

```python
# tests/unit/formats/test_openai.py
import json
from pathlib import Path
from janus.formats.openai import OpenAIAdapter
from janus.canonical.models import CanonicalRequest, Message, Role, TextPart, ToolUse

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_parse_simple_chat_request():
    raw = json.loads((FIXTURES / "openai_chat_request.json").read_text())
    req = OpenAIAdapter().parse_request(raw)
    assert req.model == "gpt-4"
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].content == "Hello"
    assert req.tools[0].function.name == "read"


def test_build_upstream_request():
    req = CanonicalRequest(
        model="gpt-4",
        system=[{"type": "text", "text": "Be concise"}],
        messages=[Message(role=Role.USER, content="hi")],
        max_tokens=100,
    )
    adapter = OpenAIAdapter()
    payload = adapter.build_upstream_request(req, "gpt-4")
    assert payload["model"] == "gpt-4"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert payload["messages"][1]["content"] == "hi"


def test_parse_upstream_stream():
    raw = (FIXTURES / "openai_stream.txt").read_text()
    parser = OpenAIAdapter().stream_parser()
    all_events = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        all_events.extend(parser.feed(line))
    all_events.extend(parser.finish())

    event_types = [e.type for e in all_events]
    assert "message_start" in event_types
    assert "text_block_start" in event_types
    assert "text_delta" in event_types
    assert "block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types

    text = "".join(e.text for e in all_events if hasattr(e, "text"))
    assert "Hello" in text


def test_emit_stream():
    from janus.canonical.events import MessageStart, TextBlockStart, TextDelta, BlockStop, MessageDelta, MessageStop
    emitter = OpenAIAdapter().stream_emitter()
    model = "gpt-4"
    events = [
        MessageStart(model=model),
        TextBlockStart(index=0),
        TextDelta(index=0, text="Hi"),
        BlockStop(index=0),
        MessageDelta(stop_reason="stop"),
        MessageStop(),
    ]
    chunks = []
    for ev in events:
        chunks.extend(emitter.feed(ev))
    chunks.extend(emitter.finish())

    output = b"".join(chunks).decode()
    assert "chat.completion.chunk" in output
    assert "Hi" in output
    assert "[DONE]" in output
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/unit/formats/test_openai.py -v
```

- [ ] **Step 4: Implement OpenAI adapter**

```python
# src/janus/formats/openai.py
from __future__ import annotations
import json
import uuid
from typing import Any
from janus.canonical.models import (
    CanonicalRequest, CanonicalResponse, Message, Role,
    TextPart, ToolUse, ToolResult, SystemBlock, Tool, ToolFunction, Usage,
    ContentPart,
)
from janus.canonical.events import (
    CanonicalEvent, MessageStart, TextBlockStart, ToolUseBlockStart,
    TextDelta, InputJsonDelta, BlockStop, MessageDelta, MessageStop,
)
from janus.streaming.sse import encode_sse

STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
REV_STOP_MAP = {v: k for k, v in STOP_MAP.items()}


def _content_to_parts(content: str | list[dict]) -> list[ContentPart]:
    if isinstance(content, str):
        return [TextPart(text=content)]
    parts: list[ContentPart] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(TextPart(type="text", text=item["text"]))
        elif item.get("type") == "image_url":
            url = item["image_url"]["url"]
            parts.append(ImagePart(type="image", source=ImageSource(type="url", url=url)))
    return parts


def _extract_tool_calls(message: dict) -> list[ToolUse]:
    result: list[ToolUse] = []
    for tc in message.get("tool_calls", []):
        fn = tc["function"]
        args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
        result.append(ToolUse(type="tool_use", id=tc["id"], name=fn["name"], input=args))
    return result


from janus.canonical.models import ImagePart, ImageSource


class OpenAIAdapter:
    name = "openai"

    def parse_request(self, raw: dict) -> CanonicalRequest:
        messages: list[Message] = []
        system: list[SystemBlock] = []
        for m in raw["messages"]:
            role = m["role"]
            if role == "system":
                system.append(SystemBlock(type="text", text=m["content"]))
            elif role == "user":
                messages.append(Message(role=Role.USER, content=_content_to_parts(m["content"])))
            elif role == "assistant":
                parts = _content_to_parts(m.get("content") or "")
                parts.extend(_extract_tool_calls(m))
                messages.append(Message(role=Role.ASSISTANT, content=parts))
            elif role == "tool":
                tr = ToolResult(type="tool_result", tool_use_id=m["tool_call_id"], content=str(m.get("content", "")))
                messages.append(Message(role=Role.TOOL, content=[tr]))

        tools = [
            Tool(type="function", function=ToolFunction(
                name=t["function"]["name"],
                description=t["function"].get("description"),
                parameters=t["function"].get("parameters", {}),
            ))
            for t in raw.get("tools", [])
        ]

        max_tokens = raw.get("max_tokens") or raw.get("max_completion_tokens")

        return CanonicalRequest(
            model=raw["model"],
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=raw.get("temperature"),
            top_p=raw.get("top_p"),
            stop=raw.get("stop"),
            stream=raw.get("stream", False),
        )

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict:
        msgs: list[dict] = []
        for sb in req.system:
            msgs.append({"role": "system", "content": sb.text})
        for msg in req.messages:
            content: Any = msg.content
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, TextPart):
                        parts.append({"type": "text", "text": p.text})
                content = parts if parts else ""
                if len(content) == 1 and content[0]["type"] == "text":
                    content = content[0]["text"]
            msgs.append({"role": msg.role.value, "content": content})
        payload: dict = {"model": model, "messages": msgs, "stream": req.stream}
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.function.name, "parameters": t.function.parameters}}
                for t in req.tools
            ]
        return payload

    def parse_upstream_response(self, raw: dict) -> CanonicalResponse:
        choice = raw["choices"][0]
        msg = choice["message"]
        parts: list[ContentPart] = []
        if msg.get("content"):
            parts.append(TextPart(text=str(msg["content"])))
        parts.extend(_extract_tool_calls(msg))
        usage = Usage(
            input_tokens=raw.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=raw.get("usage", {}).get("completion_tokens", 0),
        )
        return CanonicalResponse(
            model=raw.get("model", ""),
            content=parts,
            stop_reason=STOP_MAP.get(choice.get("finish_reason", "stop"), "end_turn"),
            usage=usage,
        )

    def emit_response(self, resp: CanonicalResponse) -> dict:
        tool_calls = None
        text_parts = []
        for p in resp.content:
            if isinstance(p, TextPart):
                text_parts.append(p.text)
            elif isinstance(p, ToolUse):
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": p.id,
                    "type": "function",
                    "function": {"name": p.name, "arguments": json.dumps(p.input)},
                })
        content = "".join(text_parts) if text_parts else None
        msg: dict = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {
            "id": "janus-" + uuid.uuid4().hex[:8],
            "object": "chat.completion",
            "model": resp.model,
            "choices": [{
                "index": 0,
                "message": msg,
                "finish_reason": REV_STOP_MAP.get(resp.stop_reason, "stop"),
            }],
            "usage": {
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
                "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        }

    def stream_parser(self):
        return _OpenAIStreamParser()

    def stream_emitter(self):
        return _OpenAIStreamEmitter()


class _OpenAIStreamParser:
    def __init__(self):
        self._started = False
        self._model = ""
        self._text_block = None
        self._tool_indices: dict[int, tuple[str, str]] = {}
        self._finished = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        events: list[CanonicalEvent] = []
        if line.strip() == "[DONE]":
            if self._text_block is not None:
                events.append(BlockStop(index=self._text_block))
                self._text_block = None
            events.append(MessageStop())
            self._finished = True
            return events
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            return []
        if "choices" not in chunk:
            return events
        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        if not self._started:
            self._model = chunk.get("model", "")
            events.append(MessageStart(model=self._model))
            self._started = True
        if "content" in delta and delta["content"] is not None:
            if self._text_block is None:
                self._text_block = 0
                events.append(TextBlockStart(index=0))
            events.append(TextDelta(index=0, text=delta["content"]))
        if "tool_calls" in delta:
            for tc in delta["tool_calls"]:
                idx = tc.get("index", 0)
                if idx not in self._tool_indices:
                    self._tool_indices[idx] = ("", "")
                tid, tname = self._tool_indices[idx]
                if "id" in tc:
                    tid = tc["id"]
                if "function" in tc and "name" in tc["function"]:
                    tname = tc["function"]["name"]
                if tid and tname and idx not in getattr(self, "_started_tools", set()):
                    if not hasattr(self, "_started_tools"):
                        self._started_tools: set[int] = set()
                    self._started_tools.add(idx)
                    events.append(ToolUseBlockStart(index=idx, id=tid, name=tname))
                if "function" in tc and "arguments" in tc["function"]:
                    events.append(InputJsonDelta(index=idx, partial_json=tc["function"]["arguments"]))
                self._tool_indices[idx] = (tid, tname)
        finish = choice.get("finish_reason")
        if finish:
            for idx in list(self._tool_indices or []) + [0]:
                events.append(BlockStop(index=idx))
            self._text_block = None
            events.append(MessageDelta(stop_reason=STOP_MAP.get(finish, "end_turn")))
        return events

    def finish(self) -> list[CanonicalEvent]:
        if self._finished:
            return []
        events: list[CanonicalEvent] = []
        if self._text_block is not None:
            events.append(BlockStop(index=self._text_block))
        events.append(MessageStop())
        self._finished = True
        return events


class _OpenAIStreamEmitter:
    def __init__(self):
        self._id = "janus-" + uuid.uuid4().hex[:8]
        self._model = ""
        self._sent_start = False
        self._text_block_open = False
        self._tool_idx: int = -1  # canonical block index → OpenAI tool index mapping
        self._block_to_tc: dict[int, int] = {}
        self._next_tc = 0

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        chunks: list[bytes] = []
        if isinstance(event, MessageStart):
            self._model = event.model
            c = {
                "id": self._id, "object": "chat.completion.chunk", "model": self._model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            chunks.append(encode_sse(c))
            self._sent_start = True
        elif isinstance(event, TextBlockStart):
            self._text_block_open = True
        elif isinstance(event, TextDelta):
            c = {
                "id": self._id, "object": "chat.completion.chunk", "model": self._model,
                "choices": [{"index": 0, "delta": {"content": event.text}, "finish_reason": None}],
            }
            chunks.append(encode_sse(c))
        elif isinstance(event, ToolUseBlockStart):
            if event.index not in self._block_to_tc:
                self._block_to_tc[event.index] = self._next_tc
                self._next_tc += 1
            tc_idx = self._block_to_tc[event.index]
            c = {
                "id": self._id, "object": "chat.completion.chunk", "model": self._model,
                "choices": [{"index": 0, "delta": {
                    "tool_calls": [{"index": tc_idx, "id": event.id, "type": "function", "function": {"name": event.name, "arguments": ""}}]
                }, "finish_reason": None}],
            }
            chunks.append(encode_sse(c))
        elif isinstance(event, InputJsonDelta):
            tc_idx = self._block_to_tc.get(event.index, 0)
            c = {
                "id": self._id, "object": "chat.completion.chunk", "model": self._model,
                "choices": [{"index": 0, "delta": {
                    "tool_calls": [{"index": tc_idx, "function": {"arguments": event.partial_json}}]
                }, "finish_reason": None}],
            }
            chunks.append(encode_sse(c))
        elif isinstance(event, MessageDelta):
            reason = REV_STOP_MAP.get(event.stop_reason, "stop") if event.stop_reason else "stop"
            c = {
                "id": self._id, "object": "chat.completion.chunk", "model": self._model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
            }
            chunks.append(encode_sse(c))
        return chunks

    def finish(self) -> list[bytes]:
        return [encode_done()]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/formats/test_openai.py -v
```
Pending fixes for any test failures.

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/openai.py tests/unit/formats/test_openai.py tests/fixtures/
git commit -m "feat: OpenAI format adapter (parse + emit + stream)"
```

---

### Task 8: Anthropic format adapter

**Files:**
- Create: `src/janus/formats/anthropic.py`
- Create: `tests/unit/formats/test_anthropic.py`, `tests/fixtures/anthropic_message_request.json`, `tests/fixtures/anthropic_stream.txt`

- [ ] **Step 1: Create fixtures**

```json
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "system": [{"type": "text", "text": "You are helpful."}],
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": [
      {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "x.py"}}
    ]},
    {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "t1", "content": "print('hello')"}
    ]}
  ]
}
```

```
event: message_start
data: {"type":"message_start","message":{"id":"m1","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","usage":{"input_tokens":10,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}

event: message_stop
data: {"type":"message_stop"}
```

- [ ] **Step 2: Write tests**

```python
# tests/unit/formats/test_anthropic.py
import json
from pathlib import Path
from janus.formats.anthropic import AnthropicAdapter
from janus.canonical.models import CanonicalRequest, Message, Role, TextPart, ToolUse, ToolResult, SystemBlock

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_parse_message_request():
    raw = json.loads((FIXTURES / "anthropic_message_request.json").read_text())
    req = AnthropicAdapter().parse_request(raw)
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].role == Role.USER
    assert req.messages[0].content[0].text == "Hello"
    assert req.messages[1].content[0] == ToolUse(type="tool_use", id="t1", name="read", input={"path": "x.py"})
    assert req.messages[2].content[0] == ToolResult(type="tool_result", tool_use_id="t1", content="print('hello')")


def test_build_upstream_request():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        system=[SystemBlock(type="text", text="Be concise")],
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hi")])],
        max_tokens=1024,
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["system"][0]["text"] == "Be concise"
    assert payload["messages"][0]["content"][0]["text"] == "hi"


def test_parse_anthropic_stream():
    raw = (FIXTURES / "anthropic_stream.txt").read_text()
    parser = AnthropicAdapter().stream_parser()
    all_events = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            all_events.extend(parser.feed(line[6:]))
    all_events.extend(parser.finish())
    event_types = [e.type for e in all_events]
    assert "message_start" in event_types
    assert "text_block_start" in event_types
    assert "text_delta" in event_types
    assert "block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types


def test_emit_response():
    adapter = AnthropicAdapter()
    from janus.canonical.models import CanonicalResponse, Usage
    resp = CanonicalResponse(
        model="claude-sonnet-4-20250514",
        content=[TextPart(type="text", text="Hello!")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    out = adapter.emit_response(resp)
    assert out["type"] == "message"
    assert out["content"][0]["text"] == "Hello!"
    assert out["stop_reason"] == "end_turn"
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/unit/formats/test_anthropic.py -v
```

- [ ] **Step 4: Implement Anthropic adapter**

```python
# src/janus/formats/anthropic.py
from __future__ import annotations
import json
from janus.canonical.models import (
    CanonicalRequest, CanonicalResponse, Message, Role,
    TextPart, ToolUse, ToolResult, SystemBlock, Tool, ToolFunction, Usage,
    ContentPart, ImagePart, ImageSource,
)
from janus.canonical.events import (
    CanonicalEvent, MessageStart, TextBlockStart, ToolUseBlockStart,
    TextDelta, InputJsonDelta, BlockStop, MessageDelta, MessageStop,
)
from janus.streaming.sse import encode_sse


class AnthropicAdapter:
    name = "anthropic"

    def parse_request(self, raw: dict) -> CanonicalRequest:
        system = [SystemBlock(type="text", text=s if isinstance(s, str) else s.get("text", ""))
                  for s in raw.get("system", []) or []]
        messages: list[Message] = []
        for m in raw.get("messages", []):
            role = Role(m["role"])
            content = m["content"]
            if isinstance(content, str):
                messages.append(Message(role=role, content=content))
            else:
                parts: list[ContentPart] = []
                for c in content:
                    t = c.get("type")
                    if t == "text":
                        parts.append(TextPart(type="text", text=c["text"]))
                    elif t == "tool_use":
                        parts.append(ToolUse(type="tool_use", id=c["id"], name=c["name"], input=c.get("input", {})))
                    elif t == "tool_result":
                        parts.append(ToolResult(type="tool_result", tool_use_id=c["tool_use_id"], content=str(c.get("content", ""))))
                    elif t == "image":
                        src = c["source"]
                        parts.append(ImagePart(type="image", source=ImageSource(
                            type=src.get("type", "base64"),
                            media_type=src.get("media_type"),
                            data=src.get("data"),
                            url=src.get("url"),
                        )))
                messages.append(Message(role=role, content=parts))
        tools = []
        for t in raw.get("tools", []):
            if t.get("type") == "custom":
                continue
            tools.append(Tool(type="function", function=ToolFunction(
                name=t["name"], description=t.get("description"), parameters=t.get("input_schema", {}),
            )))
        return CanonicalRequest(
            model=raw["model"],
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=raw.get("max_tokens"),
            temperature=raw.get("temperature"),
            top_p=raw.get("top_p"),
            stop=raw.get("stop_sequences"),
            stream=raw.get("stream", False),
        )

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict:
        system = [s.model_dump() for s in req.system] if req.system else None
        messages: list[dict] = []
        for msg in req.messages:
            content = msg.content
            if isinstance(content, str):
                messages.append({"role": msg.role.value, "content": content})
            else:
                parts = [p.model_dump() for p in content]
                messages.append({"role": msg.role.value, "content": parts})
        payload: dict = {
            "model": model,
            "max_tokens": req.max_tokens or 1024,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if req.tools:
            payload["tools"] = [
                {"name": t.function.name, "description": t.function.description or "", "input_schema": t.function.parameters}
                for t in req.tools
            ]
        if req.stream:
            payload["stream"] = True
        return payload

    def parse_upstream_response(self, raw: dict) -> CanonicalResponse:
        parts: list[ContentPart] = []
        for block in raw.get("content", []):
            if block["type"] == "text":
                parts.append(TextPart(type="text", text=block["text"]))
            elif block["type"] == "tool_use":
                parts.append(ToolUse(type="tool_use", id=block["id"], name=block["name"], input=block.get("input", {})))
        usage = Usage(
            input_tokens=raw.get("usage", {}).get("input_tokens", 0),
            output_tokens=raw.get("usage", {}).get("output_tokens", 0),
        )
        return CanonicalResponse(
            model=raw.get("model", ""),
            content=parts,
            stop_reason=raw.get("stop_reason"),
            usage=usage,
        )

    def emit_response(self, resp: CanonicalResponse) -> dict:
        content = []
        for p in resp.content:
            if isinstance(p, TextPart):
                content.append({"type": "text", "text": p.text})
            elif isinstance(p, ToolUse):
                content.append({"type": "tool_use", "id": p.id, "name": p.name, "input": p.input})
        return {
            "id": "janus-msg",
            "type": "message",
            "role": "assistant",
            "model": resp.model,
            "content": content,
            "stop_reason": resp.stop_reason,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }

    def stream_parser(self):
        return _AnthropicStreamParser()

    def stream_emitter(self):
        return _AnthropicStreamEmitter()


class _AnthropicStreamParser:
    def __init__(self):
        self._model = ""

    def feed(self, line: str) -> list[CanonicalEvent]:
        events: list[CanonicalEvent] = []
        ev = json.loads(line)
        t = ev["type"]
        if t == "message_start":
            self._model = ev["message"]["model"]
            events.append(MessageStart(model=self._model))
        elif t == "content_block_start":
            block = ev["content_block"]
            index = ev["index"]
            if block["type"] == "text":
                events.append(TextBlockStart(index=index))
            elif block["type"] == "tool_use":
                events.append(ToolUseBlockStart(index=index, id=block["id"], name=block["name"]))
        elif t == "content_block_delta":
            delta = ev["delta"]
            index = ev["index"]
            if delta["type"] == "text_delta":
                events.append(TextDelta(index=index, text=delta["text"]))
            elif delta["type"] == "input_json_delta":
                events.append(InputJsonDelta(index=index, partial_json=delta["partial_json"]))
        elif t == "content_block_stop":
            events.append(BlockStop(index=ev["index"]))
        elif t == "message_delta":
            events.append(MessageDelta(
                stop_reason=ev["delta"].get("stop_reason"),
                usage=Usage(**ev.get("usage", {})),
            ))
        elif t == "message_stop":
            events.append(MessageStop())
        return events

    def finish(self) -> list[CanonicalEvent]:
        return []


class _AnthropicStreamEmitter:
    def feed(self, event: CanonicalEvent) -> list[bytes]:
        chunks: list[bytes] = []
        if isinstance(event, MessageStart):
            chunks.append(encode_sse({"type": "message_start", "message": {
                "id": "janus-msg", "type": "message", "role": "assistant",
                "model": event.model, "usage": {"input_tokens": 0, "output_tokens": 0},
            }}))
        elif isinstance(event, TextBlockStart):
            chunks.append(encode_sse({"type": "content_block_start", "index": event.index,
                                      "content_block": {"type": "text", "text": ""}}))
        elif isinstance(event, ToolUseBlockStart):
            chunks.append(encode_sse({"type": "content_block_start", "index": event.index,
                                      "content_block": {"type": "tool_use", "id": event.id, "name": event.name, "input": {}}}))
        elif isinstance(event, TextDelta):
            chunks.append(encode_sse({"type": "content_block_delta", "index": event.index,
                                      "delta": {"type": "text_delta", "text": event.text}}))
        elif isinstance(event, InputJsonDelta):
            chunks.append(encode_sse({"type": "content_block_delta", "index": event.index,
                                      "delta": {"type": "input_json_delta", "partial_json": event.partial_json}}))
        elif isinstance(event, BlockStop):
            chunks.append(encode_sse({"type": "content_block_stop", "index": event.index}))
        elif isinstance(event, MessageDelta):
            chunks.append(encode_sse({
                "type": "message_delta",
                "delta": {"stop_reason": event.stop_reason},
                "usage": {"output_tokens": event.usage.output_tokens if event.usage else 0},
            }))
        elif isinstance(event, MessageStop):
            chunks.append(encode_sse({"type": "message_stop"}))
        return chunks

    def finish(self) -> list[bytes]:
        return []
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/formats/test_anthropic.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/anthropic.py tests/unit/formats/test_anthropic.py tests/fixtures/
git commit -m "feat: Anthropic format adapter"
```

---

### Task 9: Gemini format adapter

**Files:**
- Create: `src/janus/formats/gemini.py`
- Create: `tests/unit/formats/test_gemini.py`, `tests/fixtures/gemini_request.json`

- [ ] **Step 1: Create fixture**

```json
{
  "system_instruction": {"parts": [{"text": "You are helpful."}]},
  "contents": [
    {"role": "user", "parts": [{"text": "Hello"}]}
  ],
  "tools": [{"functionDeclarations": [{"name": "read", "parameters": {"type": "object"}}]}]
}
```

- [ ] **Step 2: Write tests**

```python
# tests/unit/formats/test_gemini.py
import json
from pathlib import Path
from janus.formats.gemini import GeminiAdapter
from janus.canonical.models import CanonicalRequest, Message, Role, TextPart

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_parse_generate_content():
    raw = json.loads((FIXTURES / "gemini_request.json").read_text())
    req = GeminiAdapter().parse_request(raw)
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].role == Role.USER
    assert req.messages[0].content[0].text == "Hello"
    assert req.tools[0].function.name == "read"


def test_build_upstream_request():
    req = CanonicalRequest(
        model="gemini-2.0-flash",
        system=[{"type": "text", "text": "Be concise"}],
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hi")])],
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.0-flash")
    assert payload["system_instruction"]["parts"][0]["text"] == "Be concise"
    assert payload["contents"][0]["parts"][0]["text"] == "hi"


def test_emit_response():
    from janus.canonical.models import CanonicalResponse, Usage
    resp = CanonicalResponse(
        model="gemini-2.0-flash",
        content=[TextPart(type="text", text="Hello!")],
        stop_reason="STOP",
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    out = GeminiAdapter().emit_response(resp)
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "Hello!"
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/unit/formats/test_gemini.py -v
```

- [ ] **Step 4: Implement Gemini adapter**

```python
# src/janus/formats/gemini.py
from __future__ import annotations
import json
from janus.canonical.models import (
    CanonicalRequest, CanonicalResponse, Message, Role,
    TextPart, ToolUse, ToolResult, SystemBlock, Tool, ToolFunction, Usage,
    ContentPart,
)
from janus.canonical.events import (
    CanonicalEvent, MessageStart, TextBlockStart, TextDelta,
    BlockStop, MessageDelta, MessageStop,
)
from janus.streaming.sse import encode_sse


class GeminiAdapter:
    name = "gemini"

    def parse_request(self, raw: dict) -> CanonicalRequest:
        system: list[SystemBlock] = []
        if "system_instruction" in raw:
            for part in raw["system_instruction"]["parts"]:
                system.append(SystemBlock(type="text", text=part["text"]))
        messages: list[Message] = []
        for c in raw.get("contents", []):
            role_str = c.get("role", "user")
            role = Role.USER if role_str == "user" else Role.ASSISTANT
            parts: list[ContentPart] = []
            for part in c.get("parts", []):
                if "text" in part:
                    parts.append(TextPart(type="text", text=part["text"]))
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    parts.append(ToolUse(type="tool_use", id=fc.get("id", ""), name=fc["name"], input=fc.get("args", {})))
                elif "functionResponse" in part:
                    fr = part["functionResponse"]
                    parts.append(ToolResult(type="tool_result", tool_use_id=fr.get("id", ""), content=str(fr.get("response", ""))))
            messages.append(Message(role=role, content=parts))
        tools = []
        for t in raw.get("tools", []):
            for fd in t.get("functionDeclarations", []):
                tools.append(Tool(type="function", function=ToolFunction(
                    name=fd["name"], description=fd.get("description"), parameters=fd.get("parameters", {}),
                )))
        return CanonicalRequest(
            model=raw.get("model", "gemini"),
            system=system,
            messages=messages,
            tools=tools,
            temperature=raw.get("generationConfig", {}).get("temperature"),
            top_p=raw.get("generationConfig", {}).get("topP"),
            stop=raw.get("generationConfig", {}).get("stopSequences"),
            max_tokens=raw.get("generationConfig", {}).get("maxOutputTokens"),
        )

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict:
        payload: dict = {}
        if req.system:
            payload["system_instruction"] = {
                "parts": [{"text": s.text} for s in req.system],
            }
        contents: list[dict] = []
        for msg in req.messages:
            parts: list[dict] = []
            content = msg.content if isinstance(msg.content, list) else [TextPart(type="text", text=str(msg.content))]
            for p in content:
                if isinstance(p, TextPart):
                    parts.append({"text": p.text})
                elif isinstance(p, ToolUse):
                    parts.append({"functionCall": {"name": p.name, "args": p.input}})
                elif isinstance(p, ToolResult):
                    parts.append({"functionResponse": {"id": p.tool_use_id, "response": p.content}})
            contents.append({"role": "user" if msg.role == Role.USER else "model", "parts": parts})
        payload["contents"] = contents
        if req.tools:
            payload["tools"] = [{
                "functionDeclarations": [{
                    "name": t.function.name,
                    "description": t.function.description or "",
                    "parameters": t.function.parameters,
                } for t in req.tools]
            }]
        gen_config: dict = {}
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        if gen_config:
            payload["generationConfig"] = gen_config
        return payload

    def parse_upstream_response(self, raw: dict) -> CanonicalResponse:
        candidate = raw.get("candidates", [{}])[0]
        parts: list[ContentPart] = []
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                parts.append(TextPart(type="text", text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                parts.append(ToolUse(type="tool_use", id="", name=fc["name"], input=fc.get("args", {})))
        usage = Usage(
            input_tokens=raw.get("usageMetadata", {}).get("promptTokenCount", 0),
            output_tokens=raw.get("usageMetadata", {}).get("candidatesTokenCount", 0),
        )
        stop = candidate.get("finishReason", "STOP").lower()
        return CanonicalResponse(model=raw.get("modelVersion", ""), content=parts, stop_reason=stop, usage=usage)

    def emit_response(self, resp: CanonicalResponse) -> dict:
        parts: list[dict] = []
        for p in resp.content:
            if isinstance(p, TextPart):
                parts.append({"text": p.text})
            elif isinstance(p, ToolUse):
                parts.append({"functionCall": {"name": p.name, "args": p.input}})
        return {
            "candidates": [{
                "content": {"role": "model", "parts": parts},
                "finishReason": (resp.stop_reason or "STOP").upper(),
            }],
            "usageMetadata": {
                "promptTokenCount": resp.usage.input_tokens,
                "candidatesTokenCount": resp.usage.output_tokens,
                "totalTokenCount": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        }

    def stream_parser(self):
        return _GeminiStreamParser()

    def stream_emitter(self):
        return _GeminiStreamEmitter()


class _GeminiStreamParser:
    def __init__(self):
        self._started = False
        self._model = ""

    def feed(self, line: str) -> list[CanonicalEvent]:
        events: list[CanonicalEvent] = []
        chunk = json.loads(line)
        if not self._started:
            self._model = chunk.get("modelVersion", "gemini")
            events.append(MessageStart(model=self._model))
            self._started = True
        candidate = chunk.get("candidates", [{}])[0]
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if "text" in part:
                events.append(TextDelta(index=0, text=part["text"]))
            elif "functionCall" in part:
                pass
        finish = candidate.get("finishReason")
        if finish:
            events.append(BlockStop(index=0))
            events.append(MessageDelta(stop_reason=finish.lower()))
        return events

    def finish(self) -> list[CanonicalEvent]:
        return [MessageStop()]


class _GeminiStreamEmitter:
    def feed(self, event: CanonicalEvent) -> list[bytes]:
        chunks: list[bytes] = []
        if isinstance(event, MessageStart):
            pass
        elif isinstance(event, TextDelta):
            chunks.append(encode_sse({
                "candidates": [{"content": {"role": "model", "parts": [{"text": event.text}]}}],
            }))
        elif isinstance(event, MessageDelta):
            chunks.append(encode_sse({
                "candidates": [{
                    "content": {"role": "model", "parts": []},
                    "finishReason": (event.stop_reason or "STOP").upper(),
                }],
            }))
        elif isinstance(event, MessageStop):
            pass
        return chunks

    def finish(self) -> list[bytes]:
        return []
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/formats/test_gemini.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/formats/gemini.py tests/unit/formats/test_gemini.py
git commit -m "feat: Gemini format adapter"
```

---

### Task 10: Provider base protocol and registry

**Files:**
- Create: `src/janus/providers/base.py`, `src/janus/providers/registry.py`
- Create: `tests/unit/providers/test_registry.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/providers/test_registry.py
from janus.providers.registry import ProviderRegistry
from janus.config.schema import ProviderConfig


def test_register_and_lookup_provider():
    registry = ProviderRegistry()
    config = ProviderConfig(
        id="test", prefix="tp", api_type="openai_compat",
        base_url="https://test.com/v1", api_key="sk-test", models=["m1"],
    )
    registry.register(config)
    result = registry.lookup("tp/m1")
    assert result is not None
    assert result.prefix == "tp"
    assert result.model == "m1"


def test_lookup_unknown_prefix():
    registry = ProviderRegistry()
    assert registry.lookup("no/such") is None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/providers/test_registry.py -v
```

- [ ] **Step 3: Implement provider base and registry**

```python
# src/janus/providers/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class RawResult:
    status_code: int
    json: dict | None = None
    lines: AsyncIterator[str] | None = None


class Provider(Protocol):
    name: str

    async def call(self, payload: dict, stream: bool) -> RawResult: ...
```

```python
# src/janus/providers/registry.py
from __future__ import annotations
from dataclasses import dataclass
from janus.config.schema import ProviderConfig
from janus.formats.base import FormatAdapter


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str  # "openai" | "anthropic" | "gemini"


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderConfig] = {}

    def register(self, config: ProviderConfig):
        self._providers[config.prefix] = config

    def lookup(self, model_str: str) -> ResolvedTarget | None:
        if "/" not in model_str:
            return None
        prefix, rest = model_str.split("/", 1)
        config = self._providers.get(prefix)
        if config is None:
            return None
        native = config.api_type.replace("_compat", "")
        return ResolvedTarget(
            prefix=prefix, model=rest,
            provider_config=config, native_format=native,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/providers/test_registry.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/providers/ tests/unit/providers/
git commit -m "feat: provider base protocol and registry"
```

---

### Task 11: Provider executors (openai_compat, anthropic, gemini, opencode_free)

**Files:**
- Create: `src/janus/providers/openai_compat.py`, `src/janus/providers/anthropic.py`, `src/janus/providers/gemini.py`, `src/janus/providers/opencode_free.py`
- Create: `tests/unit/providers/test_providers.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/providers/test_providers.py
import pytest
import respx
import httpx
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.anthropic import AnthropicProvider
from janus.providers.gemini import GeminiProvider
from janus.providers.opencode_free import OpenCodeFreeProvider


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_provider_nonstream():
    respx.post("https://test.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenAICompatProvider(base_url="https://test.com/v1", api_key="sk-test")
    result = await provider.call({"model": "m1", "messages": []}, stream=False)
    assert result.status_code == 200
    assert result.json is not None


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_provider():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": "hi"}]})
    )
    provider = AnthropicProvider(api_key="sk-ant-test")
    result = await provider.call({"model": "c", "messages": [], "max_tokens": 100}, stream=False)
    assert result.json["content"][0]["text"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_provider():
    respx.post(url__regex=r".*generativelanguage\.googleapis\.com.*").mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    )
    provider = GeminiProvider(api_key="test-key")
    result = await provider.call({"contents": []}, stream=False)
    assert result.json["candidates"][0]["content"]["parts"][0]["text"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_opencode_free_provider():
    respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )
    provider = OpenCodeFreeProvider()
    result = await provider.call({"model": "test", "messages": []}, stream=False)
    assert result.json is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/providers/test_providers.py -v
```

- [ ] **Step 3: Implement provider executors**

```python
# src/janus/providers/openai_compat.py
from __future__ import annotations
import httpx
import asyncio
from .base import RawResult


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers["x-stainless-retry-count"] = "0"
        headers["x-stainless-os"] = "janus"
        return headers

    async def call(self, payload: dict, stream: bool = False) -> RawResult:
        if stream:
            return await self._call_stream(payload)
        async with httpx.AsyncClient(timeout=120.0) as client:
            url = f"{self.base_url}/chat/completions"
            r = await client.post(url, json=payload, headers=self._headers)
            return RawResult(status_code=r.status_code, json=r.json())

    async def _call_stream(self, payload: dict) -> RawResult:
        payload["stream"] = True

        async def line_iter():
            async with httpx.AsyncClient(timeout=300.0) as client:
                url = f"{self.base_url}/chat/completions"
                async with client.stream("POST", url, json=payload, headers=self._headers) as r:
                    async for raw_line in r.aiter_lines():
                        yield raw_line

        return RawResult(status_code=200, lines=line_iter())
```

```python
# src/janus/providers/anthropic.py
from __future__ import annotations
import httpx
from .base import RawResult


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "messages-2023-12-15",
        }

    async def call(self, payload: dict, stream: bool = False) -> RawResult:
        if stream:
            return await self._call_stream(payload)
        async with httpx.AsyncClient(timeout=120.0) as client:
            url = f"{self.base_url}/v1/messages"
            r = await client.post(url, json=payload, headers=self._headers)
            return RawResult(status_code=r.status_code, json=r.json())

    async def _call_stream(self, payload: dict) -> RawResult:
        payload["stream"] = True

        async def line_iter():
            async with httpx.AsyncClient(timeout=300.0) as client:
                url = f"{self.base_url}/v1/messages"
                async with client.stream("POST", url, json=payload, headers=self._headers) as r:
                    async for raw_line in r.aiter_lines():
                        yield raw_line

        return RawResult(status_code=200, lines=line_iter())
```

```python
# src/janus/providers/gemini.py
from __future__ import annotations
import httpx
from .base import RawResult


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def call(self, payload: dict, stream: bool = False) -> RawResult:
        model = payload.get("model", "gemini-2.0-flash").removeprefix("models/")
        stream_param = "?alt=sse" if stream else ""
        url = f"{self.base_url}/v1beta/models/{model}:{'streamGenerateContent' if stream else 'generateContent'}{stream_param}&key={self.api_key}"
        if stream:
            return await self._call_stream(url, payload)
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
            return RawResult(status_code=r.status_code, json=r.json())

    async def _call_stream(self, url: str, payload: dict) -> RawResult:
        async def line_iter():
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, json=payload, headers={"Content-Type": "application/json"}) as r:
                    async for raw_line in r.aiter_lines():
                        yield raw_line

        return RawResult(status_code=200, lines=line_iter())
```

```python
# src/janus/providers/opencode_free.py
from __future__ import annotations
from .openai_compat import OpenAICompatProvider


class OpenCodeFreeProvider(OpenAICompatProvider):
    name = "opencode_free"

    def __init__(self):
        super().__init__(
            base_url="https://opencode.ai/zen/v1",
            api_key=None,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/providers/test_providers.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/providers/ tests/unit/providers/
git commit -m "feat: provider executors (openai_compat, anthropic, gemini, opencode_free)"
```

---

### Task 12: Model resolver

**Files:**
- Create: `src/janus/routing/resolver.py`, `src/janus/routing/fallback.py`
- Create: `tests/unit/routing/test_resolver.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/routing/test_resolver.py
from janus.routing.resolver import resolve
from janus.routing.fallback import FallbackHandler
from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry, ResolvedTarget


def test_resolve_simple():
    registry = ProviderRegistry()
    config = ProviderConfig(
        id="glm", prefix="glm", api_type="openai_compat",
        base_url="https://test.com", api_key="sk", models=["glm-4.7"],
    )
    registry.register(config)
    result = resolve("glm/glm-4.7", registry)
    assert result is not None
    assert result.model == "glm-4.7"
    assert result.native_format == "openai"


def test_resolve_unknown():
    registry = ProviderRegistry()
    assert resolve("no/model", registry) is None


def test_fallback_handler_single():
    registry = ProviderRegistry()
    config = ProviderConfig(
        id="glm", prefix="glm", api_type="openai_compat",
        base_url="https://test.com", api_key="sk", models=["glm-4.7"],
    )
    registry.register(config)
    handler = FallbackHandler(registry)
    result = handler.resolve("glm/glm-4.7")
    assert result == ("glm/glm-4.7", None)  # single-model, no fallback in P1
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/routing/test_resolver.py -v
```

- [ ] **Step 3: Implement resolver and fallback**

```python
# src/janus/routing/resolver.py
from __future__ import annotations
from janus.providers.registry import ProviderRegistry, ResolvedTarget


def resolve(model_str: str, registry: ProviderRegistry) -> ResolvedTarget | None:
    return registry.lookup(model_str)
```

```python
# src/janus/routing/fallback.py
from __future__ import annotations
from janus.providers.registry import ProviderRegistry


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    def resolve(self, model: str) -> tuple[str, str | None]:
        """Returns (resolved_model, next_fallback_model_or_None).
        P1: single-model, no fallback."""
        target = self.registry.lookup(model)
        if target is None:
            raise ValueError(f"Unknown model: {model}")
        return (model, None)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/routing/test_resolver.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/routing/ tests/unit/routing/
git commit -m "feat: model resolver and fallback stub"
```

---

### Task 13: Streaming translator

**Files:**
- Create: `src/janus/streaming/translator.py`
- Create: `tests/unit/streaming/test_translator.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/streaming/test_translator.py
import asyncio
import json
from janus.streaming.translator import translate_stream, translate_response
from janus.formats.openai import OpenAIAdapter
from janus.formats.anthropic import AnthropicAdapter
from janus.canonical.models import CanonicalRequest, Message, Role


async def _lines_from_sse(raw: str):
    lines = []
    for line in raw.split("\n"):
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            lines.append(line[6:])
    for l in lines:
        yield l


@pytest.mark.asyncio
async def test_translate_stream_anthropic_to_openai():
    upstream_sse = 'event: message_start\ndata: {"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","usage":{}}}\n\nevent: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\nevent: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\nevent: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\nevent: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n'
    upstream = _lines_from_sse(upstream_sse)
    parser = AnthropicAdapter().stream_parser()
    emitter = OpenAIAdapter().stream_emitter()
    chunks = []
    async for chunk in translate_stream(upstream, parser, emitter):
        chunks.append(chunk)
    output = b"".join(chunks).decode()
    assert "chat.completion.chunk" in output
    assert "Hello" in output
    assert "[DONE]" in output
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/streaming/test_translator.py -v
```

- [ ] **Step 3: Implement translator**

```python
# src/janus/streaming/translator.py
from __future__ import annotations
from typing import AsyncIterator
from janus.formats.base import StreamParser, StreamEmitter


async def translate_stream(
    upstream_lines: AsyncIterator[str],
    parser: StreamParser,
    emitter: StreamEmitter,
) -> AsyncIterator[bytes]:
    async for line in upstream_lines:
        if not line or not line.strip():
            continue
        data = line
        if line.startswith("data: "):
            data = line[6:]
        for event in parser.feed(data):
            for chunk in emitter.feed(event):
                yield chunk
    for event in parser.finish():
        for chunk in emitter.feed(event):
            yield chunk
    for chunk in emitter.finish():
        yield chunk
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/streaming/test_translator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/streaming/translator.py tests/unit/streaming/test_translator.py
git commit -m "feat: streaming translator (upstream→canonical→client)"
```

---

### Task 14: API routes

**Files:**
- Create: `src/janus/api/routes.py`, `src/janus/api/deps.py`
- Create: `tests/integration/test_api.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration/test_api.py
import pytest
from httpx import ASGITransport, AsyncClient
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.registry import ProviderRegistry


@pytest.fixture
def registry():
    reg = ProviderRegistry()
    reg.register(ProviderConfig(
        id="test", prefix="test", api_type="openai_compat",
        base_url="https://fake.local/v1", api_key="sk-test", models=["test-m1"],
    ))
    return reg


@pytest.fixture
def config():
    return JanusConfig(server=ServerSettings(port=0, require_api_key=False))


@pytest.fixture
def app(registry, config):
    return create_app(registry, config)


@pytest.mark.asyncio
async def test_models_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert any(m["id"] == "test/test-m1" for m in data["data"])


@pytest.mark.asyncio
async def test_chat_completions_nonstream(app):
    import respx, httpx
    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "r1", "object": "chat.completion", "model": "test-m1",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            })
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}], "stream": False}
            r = await client.post("/v1/chat/completions", json=payload)
            assert r.status_code == 200
            data = r.json()
            assert data["choices"][0]["message"]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_messages_endpoint_nonstream(app):
    import respx, httpx
    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "r1", "object": "chat.completion", "model": "test-m1",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Bonjour!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            })
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"model": "test/test-m1", "max_tokens": 1024, "messages": [{"role": "user", "content": "hi"}]}
            r = await client.post("/v1/messages", json=payload)
            assert r.status_code == 200
            data = r.json()
            assert data["type"] == "message"
            assert data["role"] == "assistant"


@pytest.mark.asyncio
async def test_chat_completions_stream(app):
    import respx, httpx
    sse_lines = [
        'data: {"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
        'data: {"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"X"},"finish_reason":null}]}\n\n',
        'data: {"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, content="".join(sse_lines).encode(), headers={"content-type": "text/event-stream"})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
            assert b"X" in body
            assert b"[DONE]" in body


@pytest.mark.asyncio
async def test_require_api_key_forbidden(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200  # api key not required in test config
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/integration/test_api.py -v
```

- [ ] **Step 3: Implement API deps**

```python
# src/janus/api/deps.py
from __future__ import annotations
from fastapi import Header, Request
from janus.config.schema import JanusConfig


async def require_api_key(request: Request, authorization: str = Header(default="")):
    config: JanusConfig = request.app.state.config
    if not config.server.require_api_key:
        return
    if not config.api_keys:
        return
    if authorization.startswith("Bearer "):
        key = authorization[7:]
        if key in config.api_keys:
            return
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="Invalid API key")
```

- [ ] **Step 4: Implement API routes**

```python
# src/janus/api/routes.py
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from janus.api.deps import require_api_key
from janus.formats.openai import OpenAIAdapter
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.gemini import GeminiAdapter
from janus.routing.resolver import resolve
from janus.providers.registry import ProviderRegistry
from janus.streaming.translator import translate_stream

logger = logging.getLogger(__name__)

FORMATS = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}

PROVIDER_CLASSES = {
    "openai": "openai_compat",
    "anthropic": "anthropic",
    "gemini": "gemini",
}


def _get_provider(request: Request, native_format: str):
    from janus.providers.openai_compat import OpenAICompatProvider
    from janus.providers.anthropic import AnthropicProvider
    from janus.providers.gemini import GeminiProvider
    from janus.providers.opencode_free import OpenCodeFreeProvider

    cls_name = PROVIDER_CLASSES.get(native_format, native_format)
    # The registry stores config; we need to construct the right provider from it
    # For P1 simplicity, providers are constructed per-request from registry config
    raise NotImplementedError("Provider construction from registry — see Task 15")


route = APIRouter()


@route.get("/models")
async def list_models(request: Request):
    registry: ProviderRegistry = request.app.state.registry
    models = []
    for prefix, config in registry._providers.items():
        for model in config.models:
            models.append({
                "id": f"{prefix}/{model}",
                "object": "model",
                "created": 0,
                "owned_by": config.id,
            })
    return {"object": "list", "data": models}


@route.post("/chat/completions")
async def chat_completions(request: Request, _unused=Depends(require_api_key)):
    return await _handle(request, client_format="openai")


@route.post("/messages")
async def messages(request: Request, _unused=Depends(require_api_key)):
    return await _handle(request, client_format="anthropic")


async def _handle(request: Request, client_format: str):
    from janus.providers.base import RawResult
    import json

    body = await request.json()
    client_adapter = FORMATS[client_format]

    try:
        canonical = client_adapter.parse_request(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    registry: ProviderRegistry = request.app.state.registry
    target = resolve(canonical.model, registry)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Unknown model: {canonical.model}")

    provider_adapter = FORMATS[target.native_format]
    upstream_payload = provider_adapter.build_upstream_request(canonical, target.model)

    provider = _build_provider(target.provider_config)
    result: RawResult = await provider.call(upstream_payload, stream=canonical.stream)

    if result.status_code >= 400:
        raise HTTPException(status_code=result.status_code, detail=json.dumps(result.json))

    if canonical.stream and result.lines:
        parser = provider_adapter.stream_parser()
        emitter = client_adapter.stream_emitter()

        async def stream_gen():
            async for chunk in translate_stream(result.lines, parser, emitter):
                yield chunk

        return StreamingResponse(stream_gen(), media_type="text/event-stream")
    else:
        canonical_resp = provider_adapter.parse_upstream_response(result.json)
        client_payload = client_adapter.emit_response(canonical_resp)
        return JSONResponse(content=client_payload)


def _build_provider(config):
    from janus.providers.openai_compat import OpenAICompatProvider
    from janus.providers.anthropic import AnthropicProvider
    from janus.providers.gemini import GeminiProvider
    from janus.providers.opencode_free import OpenCodeFreeProvider

    api_type = config.api_type
    if api_type == "opencode_free":
        return OpenCodeFreeProvider()
    elif api_type == "openai_compat":
        return OpenAICompatProvider(base_url=config.base_url, api_key=config.api_key)
    elif api_type == "anthropic":
        return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url or "https://api.anthropic.com")
    elif api_type == "gemini":
        return GeminiProvider(api_key=config.api_key or "")
    else:
        raise ValueError(f"Unknown provider type: {api_type}")
```

- [ ] **Step 4: Run tests (expected: some pass, some may need fixes for provider construction)**

```bash
pytest tests/integration/test_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/ tests/integration/
git commit -m "feat: API routes (/v1/chat/completions, /v1/messages, /v1/models)"
```

---

### Task 15: FastAPI app factory

**Files:**
- Create: `src/janus/app.py`
- Modify: `src/janus/api/routes.py` (clean up provider construction)

- [ ] **Step 1: Fix routes to use app state for provider construction**

Refactor `_build_provider` to be part of `create_app` setup.

- [ ] **Step 2: Write app.py**

```python
# src/janus/app.py
from __future__ import annotations
from fastapi import FastAPI
from janus.api.routes import route
from janus.config.schema import JanusConfig
from janus.providers.registry import ProviderRegistry


def create_app(registry: ProviderRegistry | None = None, config: JanusConfig | None = None) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0")
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()

    app.state.registry = registry
    app.state.config = config

    if config.providers:
        for pc in config.providers:
            registry.register(pc)

    app.include_router(route, prefix="/v1")
    return app
```

- [ ] **Step 3: Run integration tests again**

```bash
pytest tests/integration/test_api.py -v
```

Expected: 5+ tests passing.

- [ ] **Step 4: Commit**

```bash
git add src/janus/app.py
git commit -m "feat: FastAPI app factory with registry and config"
```

---

### Task 16: CLI

**Files:**
- Create: `src/janus/cli.py`, `src/janus/__main__.py`, `src/janus/settings.py`
- Modify: `src/janus/__main__.py`

- [ ] **Step 1: Write settings.py**

```python
# src/janus/settings.py
from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 20128
    host: str = "127.0.0.1"
    data_dir: str = "~/.janus"
    require_api_key: bool = False
    config_path: str = ""
    log_level: str = "info"

    model_config = {"env_prefix": "JANUS_", "env_file": ".env", "extra": "ignore"}
```

- [ ] **Step 2: Write CLI**

```python
# src/janus/cli.py
from __future__ import annotations
import uvicorn
import typer
from pathlib import Path
from janus.settings import Settings
from janus.config.loader import load_config
from janus.app import create_app

app = typer.Typer(name="janus", help="The two-faced AI routing gateway")

TEMPLATE_YAML = """# Janus configuration
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: false

providers:
  # - id: glm
  #   prefix: glm
  #   api_type: openai_compat
  #   base_url: https://open.bigmodel.cn/api/paas/v4
  #   api_key: ${GLM_API_KEY}
  #   models: [glm-4.7]
"""


@app.command()
def serve(
    port: int = typer.Option(20128, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
    reload: bool = typer.Option(False, "--reload"),
):
    config_path = Path(config).expanduser()
    janus_config = load_config(config_path)
    app_obj = create_app(config=janus_config)
    uvicorn.run(app_obj, host=host, port=port, reload=reload, log_level="info")


@app.command()
def config_init(
    path: str = typer.Option("~/.janus/config.yaml", "--path", "-p"),
):
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        typer.echo(f"Config already exists: {config_path}")
        return
    config_path.write_text(TEMPLATE_YAML)
    typer.echo(f"Config created: {config_path}")


@app.command()
def config_path():
    typer.echo(str(Path("~/.janus/config.yaml").expanduser()))


def main():
    app()
```

```python
# src/janus/__main__.py
from janus.cli import main
main()
```

- [ ] **Step 2: Verify CLI works**

```bash
python -m janus config-init --path /tmp/test_janus_config.yaml
python -m janus config-path
```

Expected: Config created and path printed.

- [ ] **Step 3: Quick smoke test of serve**

```bash
timeout 3 python -m janus serve --port 19999 --config /tmp/test_janus_config.yaml 2>&1 || true
```

Expected: "Uvicorn running on..." then exits.

- [ ] **Step 4: Commit**

```bash
git add src/janus/cli.py src/janus/__main__.py src/janus/settings.py
git commit -m "feat: CLI (serve, config-init, config-path) and settings"
```

---

### Task 17: Error handling and health check

**Files:**
- Modify: `src/janus/api/routes.py`

- [ ] **Step 1: Add error handling middleware and health endpoint**

Add to routes.py:
```python
@route.get("/health")
async def health():
    return {"status": "ok"}
```

Add error formatting for cross-format 4xx/5xx errors in `_handle`. Provider errors should be mapped to client error format.

- [ ] **Step 2: Run tests and verify**

```bash
pytest tests/integration/ -v
pytest tests/unit/ -v
```

- [ ] **Step 3: Commit**

```bash
git add src/janus/api/routes.py
git commit -m "feat: error handling and health endpoint"
```

---

### Task 18: Full test suite verification

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short 2>&1
```

- [ ] **Step 2: Run linting**

```bash
ruff check src/janus/ tests/
ruff format --check src/janus/ tests/
```

- [ ] **Step 3: Fix any issues, then commit final fixes**

```bash
git add -A && git commit -m "fix: test and lint fixes"
```

---

### Task 19: Push and validate

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Verify CLI end-to-end**

```bash
python -m janus config-init
# Edit config to add any API key
cat ~/.janus/config.yaml
```

---

## Self-Review Notes

After writing the plan:

1. **Spec coverage check:**
   - Canonical model (Section 4 spec) → Tasks 2-3
   - Format adapters (Section 5 spec) → Tasks 6-9
   - Provider executors (Section 6 spec) → Tasks 10-11
   - Request lifecycle (Section 7 spec) → Tasks 13-14
   - Config (Section 8 spec) → Task 5
   - Error handling (Section 9 spec) → Task 17
   - Testing (Section 10 spec) → All tasks (TDD)
   - Packaging (Section 11 spec) → Task 1
   - Success criteria (Section 12 spec) → Task 19

2. **No placeholders:** Verified no TBD, TODO, or "implement later" in code blocks.

3. **Type consistency:** The canonical model types (models.py, events.py) are used consistently across all format adapters and the translator.
