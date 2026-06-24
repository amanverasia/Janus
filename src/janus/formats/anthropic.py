from __future__ import annotations

import json
import uuid
from typing import Any

from janus.canonical.events import (
    BlockStop,
    CanonicalEvent,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
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
)
from janus.streaming.sse import encode_sse


class AnthropicStreamParser:
    """Parses Anthropic SSE events into canonical streaming events."""

    def __init__(self) -> None:
        self._done = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        stripped = line.strip()
        if not stripped:
            return []

        try:
            chunk = json.loads(stripped)
        except json.JSONDecodeError:
            return []

        event_type: str = chunk.get("type", "")

        if event_type == "message_start":
            message = chunk.get("message") or {}
            usage_raw = message.get("usage") or {}
            return [
                MessageStart(
                    model=message.get("model", ""),
                ),
                MessageDelta(
                    usage=Usage(
                        input_tokens=usage_raw.get("input_tokens", 0),
                        output_tokens=usage_raw.get("output_tokens", 0),
                    ),
                ),
            ]

        if event_type == "content_block_start":
            block = chunk.get("content_block") or {}
            index = chunk.get("index", 0)
            block_type = block.get("type", "text")
            if block_type == "text":
                return [TextBlockStart(index=index)]
            if block_type == "tool_use":
                return [
                    ToolUseBlockStart(
                        index=index,
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                    )
                ]
            return []

        if event_type == "content_block_delta":
            index = chunk.get("index", 0)
            delta = chunk.get("delta") or {}
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                return [TextDelta(index=index, text=delta.get("text", ""))]
            if delta_type == "input_json_delta":
                return [
                    InputJsonDelta(index=index, partial_json=delta.get("partial_json", ""))
                ]
            return []

        if event_type == "content_block_stop":
            index = chunk.get("index", 0)
            return [BlockStop(index=index)]

        if event_type == "message_delta":
            delta = chunk.get("delta") or {}
            usage_raw = chunk.get("usage") or {}
            events: list[CanonicalEvent] = []
            usage = Usage(output_tokens=usage_raw.get("output_tokens", 0))
            stop_reason = delta.get("stop_reason")
            events.append(
                MessageDelta(stop_reason=stop_reason, usage=usage if usage_raw else None)
            )
            return events

        if event_type == "message_stop":
            self._done = True
            return [MessageStop()]

        return []

    def finish(self) -> list[CanonicalEvent]:
        if not self._done:
            self._done = True
            return [MessageStop()]
        return []


class AnthropicStreamEmitter:
    """Converts canonical streaming events into Anthropic SSE bytes."""

    def __init__(self) -> None:
        self._id = f"msg_{uuid.uuid4().hex[:12]}"
        self._model = ""
        self._input_tokens = 0
        self._started = False
        self._finished = False

    def _emit(self, event_type: str, data: dict[str, Any]) -> bytes:
        payload = {"type": event_type, **data}
        return encode_sse(payload)

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        if isinstance(event, MessageStart):
            self._model = event.model
            self._started = True
            return [
                self._emit(
                    "message_start",
                    {
                        "message": {
                            "id": self._id,
                            "type": "message",
                            "role": "assistant",
                            "model": self._model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": self._input_tokens,
                                "output_tokens": 0,
                            },
                        }
                    },
                )
            ]

        if isinstance(event, TextBlockStart):
            return [
                self._emit(
                    "content_block_start",
                    {
                        "index": event.index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            ]

        if isinstance(event, ToolUseBlockStart):
            return [
                self._emit(
                    "content_block_start",
                    {
                        "index": event.index,
                        "content_block": {
                            "type": "tool_use",
                            "id": event.id,
                            "name": event.name,
                            "input": {},
                        },
                    },
                )
            ]

        if isinstance(event, TextDelta):
            return [
                self._emit(
                    "content_block_delta",
                    {
                        "index": event.index,
                        "delta": {"type": "text_delta", "text": event.text},
                    },
                )
            ]

        if isinstance(event, InputJsonDelta):
            return [
                self._emit(
                    "content_block_delta",
                    {
                        "index": event.index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": event.partial_json,
                        },
                    },
                )
            ]

        if isinstance(event, BlockStop):
            return [self._emit("content_block_stop", {"index": event.index})]

        if isinstance(event, MessageDelta):
            data: dict[str, Any] = {
                "delta": {"stop_reason": event.stop_reason, "stop_sequence": None},
            }
            if event.usage:
                data["usage"] = {"output_tokens": event.usage.output_tokens}
            return [self._emit("message_delta", data)]

        if isinstance(event, MessageStop):
            return [self._emit("message_stop", {})]

        return []

    def finish(self) -> list[bytes]:
        if not self._finished:
            self._finished = True
        return []


class AnthropicAdapter:
    """Adapter translating between Anthropic Messages API and the canonical model."""

    name = "anthropic"

    # ---- request parsing ----

    def parse_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        model = str(raw.get("model", ""))

        system: list[SystemBlock] = []
        sys_raw = raw.get("system")
        if isinstance(sys_raw, str):
            system.append(SystemBlock(type="text", text=sys_raw))
        elif isinstance(sys_raw, list):
            for block in sys_raw:
                if isinstance(block, dict) and block.get("type") == "text":
                    system.append(SystemBlock(type="text", text=block.get("text", "")))

        messages: list[Message] = []
        for msg in raw.get("messages") or []:
            role_str = msg.get("role", "")
            role = Role.USER if role_str == "user" else Role.ASSISTANT
            parts = self._parse_content_parts(msg.get("content"))
            messages.append(Message(role=role, content=parts))

        tools = [self._parse_tool_def(t) for t in raw.get("tools") or []]
        max_tokens = raw.get("max_tokens")
        temperature = raw.get("temperature")
        top_p = raw.get("top_p")
        stop = raw.get("stop_sequences")

        return CanonicalRequest(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            temperature=float(temperature) if temperature is not None else None,
            top_p=float(top_p) if top_p is not None else None,
            stop=list(stop) if isinstance(stop, list) else None,
            stream=bool(raw.get("stream", False)),
        )

    @staticmethod
    def _parse_content_parts(content: Any) -> list[ContentPart]:
        if content is None:
            return []
        if isinstance(content, str):
            return [TextPart(text=content)]
        parts: list[ContentPart] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "text")
            if ptype == "text":
                parts.append(TextPart(text=part.get("text", "")))
            elif ptype == "tool_use":
                parts.append(
                    ToolUse(
                        id=part.get("id", ""),
                        name=part.get("name", ""),
                        input=part.get("input") or {},
                    )
                )
            elif ptype == "tool_result":
                parts.append(
                    ToolResult(
                        tool_use_id=part.get("tool_use_id", ""),
                        content=part.get("content", ""),
                    )
                )
            elif ptype == "image":
                source = part.get("source") or {}
                parts.append(
                    ImagePart(
                        source=ImageSource(
                            type=source.get("type", "base64"),
                            media_type=source.get("media_type"),
                            data=source.get("data"),
                            url=source.get("url"),
                        )
                    )
                )
        return parts

    @staticmethod
    def _parse_tool_def(tool: dict[str, Any]) -> Tool:
        return Tool(
            type="function",
            function=ToolFunction(
                name=tool.get("name", ""),
                description=tool.get("description"),
                parameters=tool.get("input_schema") or {},
            ),
        )

    # ---- upstream request building ----

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict[str, Any]:
        system: list[dict[str, Any]] = [{"type": "text", "text": b.text} for b in req.system]
        messages: list[dict[str, Any]] = [self._build_message(m) for m in req.messages]

        payload: dict[str, Any] = {"model": model, "messages": messages}
        if system:
            payload["system"] = system
        payload["max_tokens"] = req.max_tokens if req.max_tokens is not None else 4096
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop_sequences"] = req.stop
        if req.tools:
            payload["tools"] = [self._build_tool_def(t) for t in req.tools]
        return payload

    @staticmethod
    def _build_message(msg: Message) -> dict[str, Any]:
        role = "user" if msg.role == Role.USER else "assistant"
        if isinstance(msg.content, str):
            return {"role": role, "content": msg.content}

        blocks: list[dict[str, Any]] = []
        for part in msg.content:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, ToolUse):
                blocks.append(
                    {"type": "tool_use", "id": part.id, "name": part.name, "input": part.input}
                )
            elif isinstance(part, ToolResult):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": part.tool_use_id,
                        "content": part.content,
                    }
                )
            elif isinstance(part, ImagePart):
                source: dict[str, Any] = {"type": part.source.type}
                if part.source.media_type:
                    source["media_type"] = part.source.media_type
                if part.source.data:
                    source["data"] = part.source.data
                if part.source.url:
                    source["url"] = part.source.url
                blocks.append({"type": "image", "source": source})
        return {"role": role, "content": blocks}

    @staticmethod
    def _build_tool_def(tool: Tool) -> dict[str, Any]:
        return {
            "name": tool.function.name,
            "description": tool.function.description,
            "input_schema": tool.function.parameters,
        }

    # ---- response parsing ----

    def parse_upstream_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        content: list[ContentPart] = []
        for block in raw.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "text")
            if btype == "text":
                content.append(TextPart(text=block.get("text", "")))
            elif btype == "tool_use":
                content.append(
                    ToolUse(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input") or {},
                    )
                )

        usage_raw = raw.get("usage") or {}
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
        )

        return CanonicalResponse(
            model=raw.get("model", ""),
            content=content,
            stop_reason=raw.get("stop_reason"),
            usage=usage,
        )

    # ---- response emitting ----

    def emit_response(self, resp: CanonicalResponse) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for part in resp.content:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, ToolUse):
                blocks.append(
                    {"type": "tool_use", "id": part.id, "name": part.name, "input": part.input}
                )
        return {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": resp.model,
            "content": blocks,
            "stop_reason": resp.stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }

    # ---- streaming ----

    def stream_parser(self) -> AnthropicStreamParser:
        return AnthropicStreamParser()

    def stream_emitter(self) -> AnthropicStreamEmitter:
        return AnthropicStreamEmitter()
