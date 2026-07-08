from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from janus.canonical.events import (
    BlockStop,
    CanonicalEvent,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
    ReasoningBlockStart,
    ReasoningDelta,
    TextBlockStart,
    TextDelta,
    ToolUseBlockStart,
)
from janus.canonical.models import (
    CanonicalRequest,
    CanonicalResponse,
    ContentPart,
    ImagePart,
    ImageSource,
    Message,
    Role,
    SystemBlock,
    TextPart,
    Tool,
    ToolFunction,
    ToolResult,
    ToolUse,
    Usage,
    tool_result_text,
)

_DONE_TO_STOP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}

_STOP_TO_DONE: dict[str, str] = {v: k for k, v in _DONE_TO_STOP.items()}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_tool_call_args(args: object) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args:
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class OllamaStreamParser:
    """Parses Ollama NDJSON chat chunks into canonical streaming events."""

    def __init__(self) -> None:
        self._started = False
        self._text_started = False
        self._text_index = 0
        self._reasoning_started = False
        self._reasoning_index = 0
        self._next_block = 0
        self._done = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        stripped = line.strip()
        if not stripped:
            return []
        try:
            chunk = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(chunk, dict):
            return []

        events: list[CanonicalEvent] = []
        if not self._started:
            self._started = True
            events.append(MessageStart(model=chunk.get("model") or ""))

        message = chunk.get("message") or {}

        thinking = message.get("thinking")
        if thinking:
            if not self._reasoning_started:
                self._reasoning_started = True
                self._reasoning_index = self._next_block
                self._next_block += 1
                events.append(ReasoningBlockStart(index=self._reasoning_index))
            events.append(ReasoningDelta(index=self._reasoning_index, text=thinking))

        content = message.get("content")
        if content:
            if not self._text_started:
                self._text_started = True
                self._text_index = self._next_block
                self._next_block += 1
                events.append(TextBlockStart(index=self._text_index))
            events.append(TextDelta(index=self._text_index, text=content))

        has_tool_calls = False
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            has_tool_calls = True
            index = self._next_block
            self._next_block += 1
            events.append(
                ToolUseBlockStart(
                    index=index,
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                )
            )
            events.append(
                InputJsonDelta(
                    index=index,
                    partial_json=json.dumps(_parse_tool_call_args(fn.get("arguments"))),
                )
            )
            events.append(BlockStop(index=index))

        if chunk.get("done"):
            self._done = True
            if self._reasoning_started:
                events.append(BlockStop(index=self._reasoning_index))
                self._reasoning_started = False
            if self._text_started:
                events.append(BlockStop(index=self._text_index))
                self._text_started = False
            usage = Usage(
                input_tokens=int(chunk.get("prompt_eval_count") or 0),
                output_tokens=int(chunk.get("eval_count") or 0),
            )
            done_reason = chunk.get("done_reason") or "stop"
            stop = "tool_use" if has_tool_calls else _DONE_TO_STOP.get(done_reason, "end_turn")
            events.append(MessageDelta(stop_reason=stop, usage=usage))
            events.append(MessageStop())

        return events

    def finish(self) -> list[CanonicalEvent]:
        if not self._done:
            self._done = True
            return [MessageStop()]
        return []


class OllamaStreamEmitter:
    """Converts canonical streaming events into Ollama NDJSON chunks."""

    def __init__(self) -> None:
        self._model = ""
        self._tool_names: dict[int, str] = {}
        self._tool_args: dict[int, str] = {}
        self._usage: Usage | None = None
        self._stop_reason: str | None = None
        self._finished = False

    def _chunk(self, message: dict[str, Any], **extra: Any) -> bytes:
        data: dict[str, Any] = {
            "model": self._model,
            "created_at": _now_iso(),
            "message": message,
            "done": False,
            **extra,
        }
        return (json.dumps(data, separators=(",", ":")) + "\n").encode()

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        if isinstance(event, MessageStart):
            self._model = event.model
            return []

        if isinstance(event, TextDelta):
            return [self._chunk({"role": "assistant", "content": event.text})]

        if isinstance(event, ReasoningDelta):
            return [self._chunk({"role": "assistant", "content": "", "thinking": event.text})]

        if isinstance(event, ToolUseBlockStart):
            self._tool_names[event.index] = event.name
            self._tool_args[event.index] = ""
            return []

        if isinstance(event, InputJsonDelta):
            if event.index in self._tool_args:
                self._tool_args[event.index] += event.partial_json
            return []

        if isinstance(event, BlockStop):
            name = self._tool_names.pop(event.index, None)
            if name is None:
                return []
            args = _parse_tool_call_args(self._tool_args.pop(event.index, ""))
            self._stop_reason = "tool_use"
            return [
                self._chunk(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": name, "arguments": args}}],
                    }
                )
            ]

        if isinstance(event, MessageDelta):
            if event.stop_reason and self._stop_reason != "tool_use":
                self._stop_reason = event.stop_reason
            if event.usage:
                self._usage = event.usage
            return []

        if isinstance(event, MessageStop):
            return self._emit_final()

        return []

    def _emit_final(self) -> list[bytes]:
        if self._finished:
            return []
        self._finished = True
        usage = self._usage or Usage()
        data: dict[str, Any] = {
            "model": self._model,
            "created_at": _now_iso(),
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": _STOP_TO_DONE.get(self._stop_reason or "end_turn", "stop"),
            "prompt_eval_count": usage.input_tokens,
            "eval_count": usage.output_tokens,
        }
        return [(json.dumps(data, separators=(",", ":")) + "\n").encode()]

    def finish(self) -> list[bytes]:
        return self._emit_final()


class OllamaAdapter:
    """Adapter translating between the Ollama chat API and the canonical model."""

    name = "ollama"
    stream_media_type = "application/x-ndjson"

    # ---- request parsing ----

    def parse_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        model = str(raw.get("model", ""))
        system: list[SystemBlock] = []
        messages: list[Message] = []
        pending_tool_ids: list[str] = []
        call_counter = 0

        for msg in raw.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            if role == "system":
                content = msg.get("content")
                if isinstance(content, str) and content:
                    system.append(SystemBlock(text=content))
            elif role == "assistant":
                parts: list[ContentPart] = []
                content = msg.get("content")
                if isinstance(content, str) and content:
                    parts.append(TextPart(text=content))
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    call_id = f"call_{call_counter}"
                    call_counter += 1
                    pending_tool_ids.append(call_id)
                    parts.append(
                        ToolUse(
                            id=call_id,
                            name=fn.get("name") or "",
                            input=_parse_tool_call_args(fn.get("arguments")),
                        )
                    )
                messages.append(Message(role=Role.ASSISTANT, content=parts))
            elif role == "tool":
                tool_id = pending_tool_ids.pop(0) if pending_tool_ids else "call_0"
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=[
                            ToolResult(
                                tool_use_id=tool_id,
                                content=str(msg.get("content") or ""),
                            )
                        ],
                    )
                )
            else:
                user_parts: list[ContentPart] = []
                content = msg.get("content")
                if isinstance(content, str) and content:
                    user_parts.append(TextPart(text=content))
                for image in msg.get("images") or []:
                    if isinstance(image, str):
                        user_parts.append(
                            ImagePart(
                                source=ImageSource(
                                    type="base64", media_type="image/png", data=image
                                )
                            )
                        )
                messages.append(Message(role=Role.USER, content=user_parts))

        options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
        assert isinstance(options, dict)
        max_tokens = options.get("num_predict")
        temperature = options.get("temperature")
        top_p = options.get("top_p")
        stop = options.get("stop")
        if isinstance(stop, str):
            stop = [stop]

        return CanonicalRequest(
            model=model,
            system=system,
            messages=messages,
            tools=[t for t in (self._parse_tool_def(t) for t in raw.get("tools") or []) if t],
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            temperature=float(temperature) if temperature is not None else None,
            top_p=float(top_p) if top_p is not None else None,
            stop=list(stop) if isinstance(stop, list) else None,
            stream=bool(raw.get("stream", True)),
        )

    @staticmethod
    def _parse_tool_def(tool: object) -> Tool | None:
        if not isinstance(tool, dict):
            return None
        fn = tool.get("function") or {}
        if not fn.get("name"):
            return None
        return Tool(
            function=ToolFunction(
                name=fn.get("name") or "",
                description=fn.get("description"),
                parameters=fn.get("parameters") or {},
            )
        )

    # ---- upstream request building ----

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for block in req.system:
            messages.append({"role": "system", "content": block.text})
        for msg in req.messages:
            messages.extend(self._build_upstream_messages(msg))

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": req.stream,
        }
        options: dict[str, Any] = {}
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if req.top_p is not None:
            options["top_p"] = req.top_p
        if req.stop:
            options["stop"] = req.stop
        if options:
            payload["options"] = options
        if req.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.function.name,
                        "description": t.function.description,
                        "parameters": t.function.parameters,
                    },
                }
                for t in req.tools
            ]
        return payload

    @staticmethod
    def _build_upstream_messages(msg: Message) -> list[dict[str, Any]]:
        if msg.role == Role.TOOL:
            parts = msg.content if isinstance(msg.content, list) else []
            return [
                {"role": "tool", "content": tool_result_text(p.content)}
                for p in parts
                if isinstance(p, ToolResult)
            ]

        if isinstance(msg.content, str):
            return [{"role": msg.role.value, "content": msg.content}]

        text = "".join(p.text for p in msg.content if isinstance(p, TextPart))
        images = [
            p.source.data
            for p in msg.content
            if isinstance(p, ImagePart) and p.source.type == "base64" and p.source.data
        ]
        tool_calls = [
            {"function": {"name": p.name, "arguments": p.input}}
            for p in msg.content
            if isinstance(p, ToolUse)
        ]
        built: dict[str, Any] = {"role": msg.role.value, "content": text}
        if images:
            built["images"] = images
        if tool_calls:
            built["tool_calls"] = tool_calls
        return [built]

    # ---- response parsing ----

    def parse_upstream_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        message = raw.get("message") or {}
        parts: list[ContentPart] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(TextPart(text=content))
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            parts.append(
                ToolUse(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                    input=_parse_tool_call_args(fn.get("arguments")),
                )
            )

        if any(isinstance(p, ToolUse) for p in parts):
            stop = "tool_use"
        else:
            stop = _DONE_TO_STOP.get(raw.get("done_reason") or "stop", "end_turn")

        return CanonicalResponse(
            model=raw.get("model", ""),
            content=parts,
            stop_reason=stop,
            usage=Usage(
                input_tokens=int(raw.get("prompt_eval_count") or 0),
                output_tokens=int(raw.get("eval_count") or 0),
            ),
            reasoning_content=message.get("thinking"),
        )

    # ---- response emitting ----

    def emit_response(self, resp: CanonicalResponse) -> dict[str, Any]:
        text = "".join(p.text for p in resp.content if isinstance(p, TextPart))
        tool_calls = [
            {"function": {"name": p.name, "arguments": p.input}}
            for p in resp.content
            if isinstance(p, ToolUse)
        ]
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if resp.reasoning_content:
            message["thinking"] = resp.reasoning_content
        return {
            "model": resp.model,
            "created_at": _now_iso(),
            "message": message,
            "done": True,
            "done_reason": _STOP_TO_DONE.get(resp.stop_reason or "end_turn", "stop"),
            "prompt_eval_count": resp.usage.input_tokens,
            "eval_count": resp.usage.output_tokens,
        }

    # ---- streaming ----

    def stream_parser(self) -> OllamaStreamParser:
        return OllamaStreamParser()

    def stream_emitter(self) -> OllamaStreamEmitter:
        return OllamaStreamEmitter()
