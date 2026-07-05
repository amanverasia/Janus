from __future__ import annotations

import json
import time
import uuid
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
    ToolChoiceAuto,
    ToolChoiceNone,
    ToolChoiceRequired,
    ToolChoiceSpecific,
    ToolChoiceType,
    ToolFunction,
    ToolResult,
    ToolUse,
    Usage,
)

_TEXT_PART_TYPES = ("input_text", "output_text", "text", "summary_text")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _encode_event(name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode()


def _parse_args(args_str: object) -> dict[str, Any]:
    if not isinstance(args_str, str) or not args_str:
        return {}
    try:
        parsed = json.loads(args_str)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_dict(usage: Usage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "input_tokens_details": {"cached_tokens": usage.cache_read_input_tokens},
        "output_tokens": usage.output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": usage.input_tokens + usage.output_tokens,
    }


class OpenAIResponsesStreamParser:
    """Parses OpenAI Responses API SSE events into canonical streaming events."""

    def __init__(self) -> None:
        self._index_map: dict[int, int] = {}
        self._kind_map: dict[int, str] = {}
        self._next_block = 0
        self._done = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        stripped = line.strip()
        if not stripped or stripped.startswith("event:") or "[DONE]" in stripped:
            return []
        data_str = stripped[5:].strip() if stripped.startswith("data:") else stripped
        if not data_str:
            return []
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        etype = data.get("type", "")

        if etype == "response.created":
            response = data.get("response") or {}
            return [MessageStart(model=response.get("model") or "")]

        if etype == "response.output_item.added":
            return self._handle_item_added(data)

        if etype == "response.content_part.added":
            oi = int(data.get("output_index", 0))
            part = data.get("part") or {}
            if self._kind_map.get(oi) == "message" and part.get("type") == "output_text":
                return [TextBlockStart(index=self._index_map[oi])]
            return []

        if etype == "response.output_text.delta":
            oi = int(data.get("output_index", 0))
            if oi in self._index_map:
                return [TextDelta(index=self._index_map[oi], text=data.get("delta") or "")]
            return []

        if etype == "response.reasoning_summary_text.delta":
            oi = int(data.get("output_index", 0))
            if oi in self._index_map:
                return [ReasoningDelta(index=self._index_map[oi], text=data.get("delta") or "")]
            return []

        if etype == "response.function_call_arguments.delta":
            oi = int(data.get("output_index", 0))
            if oi in self._index_map:
                return [
                    InputJsonDelta(
                        index=self._index_map[oi],
                        partial_json=data.get("delta") or "",
                    )
                ]
            return []

        if etype == "response.output_item.done":
            oi = int(data.get("output_index", 0))
            if oi in self._index_map:
                return [BlockStop(index=self._index_map[oi])]
            return []

        if etype in ("response.completed", "response.incomplete", "response.failed"):
            return self._handle_final(data)

        return []

    def _handle_item_added(self, data: dict[str, Any]) -> list[CanonicalEvent]:
        oi = int(data.get("output_index", 0))
        item = data.get("item") or {}
        itype = item.get("type", "message")
        ci = self._next_block
        self._next_block += 1
        self._index_map[oi] = ci
        self._kind_map[oi] = itype
        if itype == "function_call":
            return [
                ToolUseBlockStart(
                    index=ci,
                    id=item.get("call_id") or item.get("id") or "",
                    name=item.get("name") or "",
                )
            ]
        if itype == "reasoning":
            return [ReasoningBlockStart(index=ci)]
        return []

    def _handle_final(self, data: dict[str, Any]) -> list[CanonicalEvent]:
        if self._done:
            return []
        self._done = True
        response = data.get("response") or {}
        usage_raw = response.get("usage") or {}
        usage: Usage | None = None
        if usage_raw:
            details = usage_raw.get("input_tokens_details") or {}
            usage = Usage(
                input_tokens=usage_raw.get("input_tokens", 0),
                output_tokens=usage_raw.get("output_tokens", 0),
                cache_read_input_tokens=details.get("cached_tokens", 0),
            )
        stop_reason = "end_turn"
        if response.get("status") == "incomplete":
            details_raw = response.get("incomplete_details") or {}
            if details_raw.get("reason") == "max_output_tokens":
                stop_reason = "max_tokens"
        elif any(
            isinstance(item, dict) and item.get("type") == "function_call"
            for item in response.get("output") or []
        ):
            stop_reason = "tool_use"
        return [MessageDelta(stop_reason=stop_reason, usage=usage), MessageStop()]

    def finish(self) -> list[CanonicalEvent]:
        if not self._done:
            self._done = True
            return [MessageStop()]
        return []


class OpenAIResponsesStreamEmitter:
    """Converts canonical streaming events into OpenAI Responses API SSE events."""

    def __init__(self) -> None:
        self._id = _new_id("resp")
        self._model = ""
        self._created_at = int(time.time())
        self._seq = 0
        self._next_output = 0
        self._items: dict[int, dict[str, Any]] = {}
        self._order: list[int] = []
        self._usage: Usage | None = None
        self._stop_reason: str | None = None
        self._finished = False

    def _event(self, name: str, data: dict[str, Any]) -> bytes:
        data["type"] = name
        data["sequence_number"] = self._seq
        self._seq += 1
        return _encode_event(name, data)

    def _response_body(self, status: str) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        for key in self._order:
            info = self._items[key]
            if info["kind"] == "text":
                output.append(
                    {
                        "id": info["item_id"],
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": info["text"], "annotations": []}
                        ],
                    }
                )
            elif info["kind"] == "tool":
                output.append(
                    {
                        "id": info["item_id"],
                        "type": "function_call",
                        "status": "completed",
                        "call_id": info["call_id"],
                        "name": info["name"],
                        "arguments": info["args"],
                    }
                )
            else:
                output.append(
                    {
                        "id": info["item_id"],
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": info["text"]}],
                    }
                )
        body: dict[str, Any] = {
            "id": self._id,
            "object": "response",
            "created_at": self._created_at,
            "status": status,
            "model": self._model,
            "output": output,
            "incomplete_details": (
                {"reason": "max_output_tokens"} if status == "incomplete" else None
            ),
        }
        if self._usage is not None:
            body["usage"] = _usage_dict(self._usage)
        return body

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        if isinstance(event, MessageStart):
            self._model = event.model
            return [
                self._event("response.created", {"response": self._response_body("in_progress")}),
                self._event(
                    "response.in_progress", {"response": self._response_body("in_progress")}
                ),
            ]

        if isinstance(event, TextBlockStart):
            info = self._add_item(event.index, "text", _new_id("msg"))
            item = {
                "id": info["item_id"],
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            return [
                self._event(
                    "response.output_item.added",
                    {"output_index": info["output_index"], "item": item},
                ),
                self._event(
                    "response.content_part.added",
                    {
                        "item_id": info["item_id"],
                        "output_index": info["output_index"],
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    },
                ),
            ]

        if isinstance(event, TextDelta):
            text_info = self._items.get(event.index)
            if text_info is None:
                return []
            text_info["text"] += event.text
            return [
                self._event(
                    "response.output_text.delta",
                    {
                        "item_id": text_info["item_id"],
                        "output_index": text_info["output_index"],
                        "content_index": 0,
                        "delta": event.text,
                    },
                )
            ]

        if isinstance(event, ReasoningBlockStart):
            info = self._add_item(event.index, "reasoning", _new_id("rs"))
            item = {"id": info["item_id"], "type": "reasoning", "summary": []}
            return [
                self._event(
                    "response.output_item.added",
                    {"output_index": info["output_index"], "item": item},
                )
            ]

        if isinstance(event, ReasoningDelta):
            rs_info = self._items.get(event.index)
            if rs_info is None:
                return []
            rs_info["text"] += event.text
            return [
                self._event(
                    "response.reasoning_summary_text.delta",
                    {
                        "item_id": rs_info["item_id"],
                        "output_index": rs_info["output_index"],
                        "summary_index": 0,
                        "delta": event.text,
                    },
                )
            ]

        if isinstance(event, ToolUseBlockStart):
            info = self._add_item(event.index, "tool", _new_id("fc"))
            info["call_id"] = event.id or _new_id("call")
            info["name"] = event.name
            item = {
                "id": info["item_id"],
                "type": "function_call",
                "status": "in_progress",
                "call_id": info["call_id"],
                "name": info["name"],
                "arguments": "",
            }
            return [
                self._event(
                    "response.output_item.added",
                    {"output_index": info["output_index"], "item": item},
                )
            ]

        if isinstance(event, InputJsonDelta):
            args_info = self._items.get(event.index)
            if args_info is None:
                return []
            args_info["args"] += event.partial_json
            return [
                self._event(
                    "response.function_call_arguments.delta",
                    {
                        "item_id": args_info["item_id"],
                        "output_index": args_info["output_index"],
                        "delta": event.partial_json,
                    },
                )
            ]

        if isinstance(event, BlockStop):
            return self._emit_block_stop(event.index)

        if isinstance(event, MessageDelta):
            if event.stop_reason:
                self._stop_reason = event.stop_reason
            if event.usage:
                self._usage = event.usage
            return []

        if isinstance(event, MessageStop):
            return self._emit_final()

        return []

    def _add_item(self, index: int, kind: str, item_id: str) -> dict[str, Any]:
        info: dict[str, Any] = {
            "kind": kind,
            "item_id": item_id,
            "output_index": self._next_output,
            "text": "",
            "args": "",
            "call_id": "",
            "name": "",
            "closed": False,
        }
        self._next_output += 1
        self._items[index] = info
        self._order.append(index)
        return info

    def _emit_block_stop(self, index: int) -> list[bytes]:
        info = self._items.get(index)
        if info is None or info["closed"]:
            return []
        info["closed"] = True
        oi = info["output_index"]
        out: list[bytes] = []
        if info["kind"] == "text":
            out.append(
                self._event(
                    "response.output_text.done",
                    {
                        "item_id": info["item_id"],
                        "output_index": oi,
                        "content_index": 0,
                        "text": info["text"],
                    },
                )
            )
            out.append(
                self._event(
                    "response.content_part.done",
                    {
                        "item_id": info["item_id"],
                        "output_index": oi,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": info["text"], "annotations": []},
                    },
                )
            )
            item: dict[str, Any] = {
                "id": info["item_id"],
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": info["text"], "annotations": []}],
            }
        elif info["kind"] == "tool":
            out.append(
                self._event(
                    "response.function_call_arguments.done",
                    {
                        "item_id": info["item_id"],
                        "output_index": oi,
                        "arguments": info["args"],
                    },
                )
            )
            item = {
                "id": info["item_id"],
                "type": "function_call",
                "status": "completed",
                "call_id": info["call_id"],
                "name": info["name"],
                "arguments": info["args"],
            }
        else:
            out.append(
                self._event(
                    "response.reasoning_summary_text.done",
                    {
                        "item_id": info["item_id"],
                        "output_index": oi,
                        "summary_index": 0,
                        "text": info["text"],
                    },
                )
            )
            item = {
                "id": info["item_id"],
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": info["text"]}],
            }
        out.append(self._event("response.output_item.done", {"output_index": oi, "item": item}))
        return out

    def _emit_final(self) -> list[bytes]:
        if self._finished:
            return []
        self._finished = True
        out: list[bytes] = []
        for index in list(self._order):
            out.extend(self._emit_block_stop(index))
        if self._stop_reason == "max_tokens":
            out.append(
                self._event("response.incomplete", {"response": self._response_body("incomplete")})
            )
        else:
            out.append(
                self._event("response.completed", {"response": self._response_body("completed")})
            )
        return out

    def finish(self) -> list[bytes]:
        return self._emit_final()


class OpenAIResponsesAdapter:
    """Adapter translating between the OpenAI Responses API and the canonical model."""

    name = "openai_responses"

    # ---- request parsing ----

    def parse_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        model = str(raw.get("model", ""))
        system: list[SystemBlock] = []
        messages: list[Message] = []

        instructions = raw.get("instructions")
        if isinstance(instructions, str) and instructions:
            system.append(SystemBlock(text=instructions))

        input_val = raw.get("input")
        if isinstance(input_val, str):
            messages.append(Message(role=Role.USER, content=[TextPart(text=input_val)]))
        elif isinstance(input_val, list):
            for item in input_val:
                if not isinstance(item, dict):
                    continue
                self._parse_input_item(item, system, messages)

        tools = [self._parse_tool_def(t) for t in raw.get("tools") or []]
        max_tokens = raw.get("max_output_tokens")
        temperature = raw.get("temperature")
        top_p = raw.get("top_p")
        reasoning = raw.get("reasoning")
        effort: str | None = None
        if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
            effort = str(reasoning["effort"])

        return CanonicalRequest(
            model=model,
            system=system,
            messages=messages,
            tools=[t for t in tools if t is not None],
            tool_choice=self._parse_tool_choice(raw.get("tool_choice")),
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            temperature=float(temperature) if temperature is not None else None,
            top_p=float(top_p) if top_p is not None else None,
            stream=bool(raw.get("stream", False)),
            reasoning_effort=effort,
        )

    def _parse_input_item(
        self,
        item: dict[str, Any],
        system: list[SystemBlock],
        messages: list[Message],
    ) -> None:
        itype = item.get("type", "message")
        if itype == "message":
            role = item.get("role", "user")
            parts = self._parse_content(item.get("content"))
            if role in ("system", "developer"):
                for part in parts:
                    if isinstance(part, TextPart):
                        system.append(SystemBlock(text=part.text))
            elif role == "assistant":
                messages.append(Message(role=Role.ASSISTANT, content=parts))
            else:
                messages.append(Message(role=Role.USER, content=parts))
        elif itype == "function_call":
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ToolUse(
                            id=item.get("call_id") or item.get("id") or "",
                            name=item.get("name") or "",
                            input=_parse_args(item.get("arguments")),
                        )
                    ],
                )
            )
        elif itype == "function_call_output":
            output = item.get("output")
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=[
                        ToolResult(
                            tool_use_id=item.get("call_id") or "",
                            content=output if isinstance(output, str) else json.dumps(output),
                        )
                    ],
                )
            )

    @staticmethod
    def _parse_content(content: object) -> list[ContentPart]:
        if content is None:
            return []
        if isinstance(content, str):
            return [TextPart(text=content)]
        parts: list[ContentPart] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type", "input_text")
                if ptype in _TEXT_PART_TYPES:
                    parts.append(TextPart(text=part.get("text") or ""))
                elif ptype == "input_image":
                    url = part.get("image_url")
                    if isinstance(url, dict):
                        url = url.get("url")
                    parts.append(ImagePart(source=ImageSource(type="url", url=str(url or ""))))
        return parts

    @staticmethod
    def _parse_tool_def(tool: object) -> Tool | None:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            return None
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        assert isinstance(fn, dict)
        return Tool(
            function=ToolFunction(
                name=fn.get("name") or "",
                description=fn.get("description"),
                parameters=fn.get("parameters") or {},
            )
        )

    @staticmethod
    def _parse_tool_choice(choice: object) -> ToolChoiceType | None:
        if isinstance(choice, str):
            if choice == "auto":
                return ToolChoiceAuto()
            if choice == "none":
                return ToolChoiceNone()
            if choice == "required":
                return ToolChoiceRequired()
            return None
        if isinstance(choice, dict) and choice.get("type") == "function":
            return ToolChoiceSpecific(name=str(choice.get("name") or ""))
        return None

    # ---- upstream request building ----

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for msg in req.messages:
            items.extend(self._build_input_items(msg))

        payload: dict[str, Any] = {"model": model, "input": items, "store": False}
        if req.system:
            payload["instructions"] = "\n\n".join(b.text for b in req.system)
        if req.stream:
            payload["stream"] = True
        if req.max_tokens is not None:
            payload["max_output_tokens"] = req.max_tokens
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t.function.name,
                    "description": t.function.description,
                    "parameters": t.function.parameters,
                }
                for t in req.tools
            ]
        if req.tool_choice is not None:
            payload["tool_choice"] = self._build_tool_choice(req.tool_choice)
        if req.reasoning_effort is not None:
            payload["reasoning"] = {"effort": req.reasoning_effort}
        return payload

    @staticmethod
    def _build_tool_choice(choice: ToolChoiceType) -> str | dict[str, Any]:
        if isinstance(choice, ToolChoiceSpecific):
            return {"type": "function", "name": choice.name}
        return choice.type

    def _build_input_items(self, msg: Message) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if msg.role == Role.TOOL:
            parts = msg.content if isinstance(msg.content, list) else []
            for part in parts:
                if isinstance(part, ToolResult):
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": part.tool_use_id,
                            "output": part.content,
                        }
                    )
            return items

        if isinstance(msg.content, str):
            content_parts: list[ContentPart] = [TextPart(text=msg.content)]
        else:
            content_parts = list(msg.content)

        text_type = "output_text" if msg.role == Role.ASSISTANT else "input_text"
        message_content: list[dict[str, Any]] = []
        for part in content_parts:
            if isinstance(part, TextPart):
                message_content.append({"type": text_type, "text": part.text})
            elif isinstance(part, ImagePart) and msg.role == Role.USER:
                message_content.append({"type": "input_image", "image_url": part.source.url or ""})
            elif isinstance(part, ToolUse):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": part.id,
                        "name": part.name,
                        "arguments": json.dumps(part.input),
                    }
                )
            elif isinstance(part, ToolResult):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": part.tool_use_id,
                        "output": part.content,
                    }
                )
        if message_content:
            items.insert(
                0,
                {"type": "message", "role": msg.role.value, "content": message_content},
            )
        return items

    # ---- response parsing ----

    def parse_upstream_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        parts: list[ContentPart] = []
        reasoning_texts: list[str] = []
        for item in raw.get("output") or []:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "message")
            if itype == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in _TEXT_PART_TYPES:
                        text = part.get("text") or ""
                        if text:
                            parts.append(TextPart(text=text))
            elif itype == "function_call":
                parts.append(
                    ToolUse(
                        id=item.get("call_id") or item.get("id") or "",
                        name=item.get("name") or "",
                        input=_parse_args(item.get("arguments")),
                    )
                )
            elif itype == "reasoning":
                for part in item.get("summary") or []:
                    if isinstance(part, dict) and part.get("text"):
                        reasoning_texts.append(str(part["text"]))

        stop_reason = "end_turn"
        if raw.get("status") == "incomplete":
            details = raw.get("incomplete_details") or {}
            if details.get("reason") == "max_output_tokens":
                stop_reason = "max_tokens"
        elif any(isinstance(p, ToolUse) for p in parts):
            stop_reason = "tool_use"

        usage_raw = raw.get("usage") or {}
        input_details = usage_raw.get("input_tokens_details") or {}
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
            cache_read_input_tokens=input_details.get("cached_tokens", 0),
        )

        return CanonicalResponse(
            model=raw.get("model", ""),
            content=parts,
            stop_reason=stop_reason,
            usage=usage,
            reasoning_content="\n".join(reasoning_texts) or None,
        )

    # ---- response emitting ----

    def emit_response(self, resp: CanonicalResponse) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        if resp.reasoning_content:
            output.append(
                {
                    "id": _new_id("rs"),
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": resp.reasoning_content}],
                }
            )
        text = "".join(p.text for p in resp.content if isinstance(p, TextPart))
        if text:
            output.append(
                {
                    "id": _new_id("msg"),
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            )
        for part in resp.content:
            if isinstance(part, ToolUse):
                output.append(
                    {
                        "id": _new_id("fc"),
                        "type": "function_call",
                        "status": "completed",
                        "call_id": part.id,
                        "name": part.name,
                        "arguments": json.dumps(part.input),
                    }
                )

        incomplete = resp.stop_reason == "max_tokens"
        return {
            "id": _new_id("resp"),
            "object": "response",
            "created_at": int(time.time()),
            "status": "incomplete" if incomplete else "completed",
            "incomplete_details": {"reason": "max_output_tokens"} if incomplete else None,
            "model": resp.model,
            "output": output,
            "usage": _usage_dict(resp.usage),
        }

    # ---- streaming ----

    def stream_parser(self) -> OpenAIResponsesStreamParser:
        return OpenAIResponsesStreamParser()

    def stream_emitter(self) -> OpenAIResponsesStreamEmitter:
        return OpenAIResponsesStreamEmitter()
