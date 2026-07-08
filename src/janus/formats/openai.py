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
    tool_result_text,
)
from janus.canonical.tool_calls import (
    fix_missing_tool_responses_openai,
    inject_reasoning_content_openai,
)
from janus.streaming.sse import encode_done, encode_sse

_FINISH_TO_STOP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}

_STOP_TO_FINISH: dict[str, str] = {v: k for k, v in _FINISH_TO_STOP.items()}


class OpenAIStreamParser:
    """Parses OpenAI SSE chunks into canonical streaming events."""

    def __init__(self) -> None:
        self._started = False
        self._text_started = False
        self._text_index = 0
        self._reasoning_started = False
        self._reasoning_index = 0
        self._tool_map: dict[int, int] = {}
        self._next_block = 0
        self._done = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        stripped = line.strip()
        if not stripped:
            return []

        if "[DONE]" in stripped:
            if not self._done:
                self._done = True
                return [MessageStop()]
            return []

        data_str = stripped[5:].strip() if stripped.startswith("data:") else stripped
        if not data_str:
            return []

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        events: list[CanonicalEvent] = []

        usage_raw = chunk.get("usage")
        if isinstance(usage_raw, dict) and usage_raw.get("prompt_tokens") is not None:
            events.append(
                MessageDelta(
                    usage=Usage(
                        input_tokens=usage_raw.get("prompt_tokens", 0),
                        output_tokens=usage_raw.get("completion_tokens", 0),
                    ),
                )
            )

        choices = chunk.get("choices") or []
        if not choices:
            return events

        choice = choices[0]
        delta: dict[str, Any] = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        if delta.get("role") == "assistant" and not self._started:
            self._started = True
            events.append(MessageStart(model=chunk.get("model") or ""))

        content = delta.get("content")
        if content is not None:
            if not self._text_started:
                self._text_started = True
                self._text_index = self._next_block
                self._next_block += 1
                events.append(TextBlockStart(index=self._text_index))
            events.append(TextDelta(index=self._text_index, text=content))

        reasoning = delta.get("reasoning_content")
        if reasoning is not None:
            if not self._reasoning_started:
                self._reasoning_started = True
                self._reasoning_index = self._next_block
                self._next_block += 1
                events.append(ReasoningBlockStart(index=self._reasoning_index))
            events.append(ReasoningDelta(index=self._reasoning_index, text=reasoning))

        tool_calls = delta.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                oai_idx = tc.get("index", 0)
                fn: dict[str, Any] = tc.get("function") or {}
                if oai_idx not in self._tool_map:
                    ci = self._next_block
                    self._next_block += 1
                    self._tool_map[oai_idx] = ci
                    events.append(
                        ToolUseBlockStart(
                            index=ci,
                            id=tc.get("id", ""),
                            name=fn.get("name", ""),
                        )
                    )
                else:
                    ci = self._tool_map[oai_idx]
                args = fn.get("arguments", "")
                if args:
                    events.append(InputJsonDelta(index=self._tool_map[oai_idx], partial_json=args))

        if finish_reason is not None:
            if self._reasoning_started:
                events.append(BlockStop(index=self._reasoning_index))
                self._reasoning_started = False
            if self._text_started:
                events.append(BlockStop(index=self._text_index))
                self._text_started = False
            for ci in self._tool_map.values():
                events.append(BlockStop(index=ci))
            self._tool_map.clear()
            events.append(
                MessageDelta(stop_reason=_FINISH_TO_STOP.get(finish_reason, finish_reason))
            )

        return events

    def finish(self) -> list[CanonicalEvent]:
        if not self._done:
            self._done = True
            return [MessageStop()]
        return []


class OpenAIStreamEmitter:
    """Converts canonical streaming events into OpenAI SSE chunks."""

    def __init__(self) -> None:
        self._id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        self._model = ""
        self._tool_indices: dict[int, int] = {}
        self._next_oai = 0
        self._finished = False

    def _make_chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
        return encode_sse(
            {
                "id": self._id,
                "object": "chat.completion.chunk",
                "model": self._model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        if isinstance(event, MessageStart):
            self._model = event.model
            return [self._make_chunk({"role": "assistant"})]

        if isinstance(event, TextBlockStart):
            return []

        if isinstance(event, ToolUseBlockStart):
            oai_idx = self._next_oai
            self._next_oai += 1
            self._tool_indices[event.index] = oai_idx
            return [
                self._make_chunk(
                    {
                        "tool_calls": [
                            {
                                "index": oai_idx,
                                "id": event.id,
                                "type": "function",
                                "function": {"name": event.name, "arguments": ""},
                            }
                        ]
                    }
                )
            ]

        if isinstance(event, TextDelta):
            return [self._make_chunk({"content": event.text})]

        if isinstance(event, ReasoningBlockStart):
            return []

        if isinstance(event, ReasoningDelta):
            return [self._make_chunk({"reasoning_content": event.text})]

        if isinstance(event, InputJsonDelta):
            oai_idx = self._tool_indices.get(event.index, 0)
            delta = {
                "tool_calls": [{"index": oai_idx, "function": {"arguments": event.partial_json}}]
            }
            return [self._make_chunk(delta)]

        if isinstance(event, BlockStop):
            return []

        if isinstance(event, MessageDelta):
            stop = event.stop_reason or "end_turn"
            finish = _STOP_TO_FINISH.get(stop, "stop")
            data: dict[str, Any] = {
                "id": self._id,
                "object": "chat.completion.chunk",
                "model": self._model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
            if event.usage:
                data["usage"] = {
                    "prompt_tokens": event.usage.input_tokens,
                    "completion_tokens": event.usage.output_tokens,
                    "total_tokens": event.usage.input_tokens + event.usage.output_tokens,
                }
            return [encode_sse(data)]

        if isinstance(event, MessageStop):
            return []

        return []

    def finish(self) -> list[bytes]:
        if not self._finished:
            self._finished = True
            return [encode_done()]
        return []


class OpenAIAdapter:
    """Adapter translating between OpenAI Chat Completions API and the canonical model."""

    name = "openai"

    # ---- request parsing ----

    def parse_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        model = str(raw.get("model", ""))
        system: list[SystemBlock] = []
        messages: list[Message] = []

        for msg in raw.get("messages") or []:
            role = msg.get("role", "")
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system.append(SystemBlock(type="text", text=content))
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            system.append(SystemBlock(type="text", text=part.get("text", "")))
            elif role == "user":
                messages.append(Message(role=Role.USER, content=self._parse_content_parts(msg)))
            elif role == "assistant":
                messages.append(self._parse_assistant(msg))
            elif role == "tool":
                messages.append(self._parse_tool(msg))

        tools = [self._parse_tool_def(t) for t in raw.get("tools") or []]
        max_tokens = raw.get("max_tokens")
        temperature = raw.get("temperature")
        top_p = raw.get("top_p")
        stop = raw.get("stop")
        reasoning_effort = self._parse_reasoning_effort(raw)

        return CanonicalRequest(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=self._parse_tool_choice(raw.get("tool_choice")),
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            temperature=float(temperature) if temperature is not None else None,
            top_p=float(top_p) if top_p is not None else None,
            stop=list(stop) if isinstance(stop, list) else None,
            stream=bool(raw.get("stream", False)),
            thinking=self._parse_thinking(raw),
            reasoning_effort=reasoning_effort,
        )

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

    @staticmethod
    def _parse_thinking(raw: dict[str, Any]) -> dict[str, str] | None:
        thinking = raw.get("thinking")
        if thinking is None:
            extra = raw.get("extra_body")
            if isinstance(extra, dict):
                thinking = extra.get("thinking")
        if not isinstance(thinking, dict):
            return None
        mode = thinking.get("type")
        if mode in ("enabled", "disabled"):
            return {"type": mode}
        return None

    @staticmethod
    def _parse_reasoning_effort(raw: dict[str, Any]) -> str | None:
        effort = raw.get("reasoning_effort")
        if effort is None:
            output_config = raw.get("output_config")
            if isinstance(output_config, dict):
                effort = output_config.get("effort")
        extra = raw.get("extra_body")
        if effort is None and isinstance(extra, dict):
            effort = extra.get("reasoning_effort")
            if effort is None:
                nested_output = extra.get("output_config")
                if isinstance(nested_output, dict):
                    effort = nested_output.get("effort")
        if effort is None:
            return None
        return str(effort)

    @staticmethod
    def _parse_content_parts(msg: dict[str, Any]) -> list[ContentPart]:
        content = msg.get("content")
        if content is None:
            return []
        if isinstance(content, str):
            return [TextPart(text=content)]
        parts: list[ContentPart] = []
        for part in content:
            ptype = part.get("type", "text")
            if ptype == "text":
                parts.append(TextPart(text=part.get("text", "")))
            elif ptype == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                parts.append(ImagePart(source=ImageSource(type="url", url=url)))
        return parts

    def _parse_assistant(self, msg: dict[str, Any]) -> Message:
        parts: list[ContentPart] = []
        content = msg.get("content")
        if isinstance(content, str) and content:
            parts.append(TextPart(text=content))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parts.append(
                ToolUse(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    input=args,
                )
            )
        return Message(
            role=Role.ASSISTANT,
            content=parts,
            reasoning_content=msg.get("reasoning_content"),
        )

    @staticmethod
    def _parse_tool(msg: dict[str, Any]) -> Message:
        tool_result = ToolResult(
            tool_use_id=msg.get("tool_call_id", ""),
            content=msg.get("content", ""),
        )
        return Message(role=Role.TOOL, content=[tool_result])

    @staticmethod
    def _parse_tool_def(tool: dict[str, Any]) -> Tool:
        fn = tool.get("function") or {}
        return Tool(
            type="function",
            function=ToolFunction(
                name=fn.get("name", ""),
                description=fn.get("description"),
                parameters=fn.get("parameters") or {},
            ),
        )

    # ---- upstream request building ----

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for block in req.system:
            messages.append({"role": "system", "content": block.text})
        for msg in req.messages:
            messages.extend(self._build_upstream_messages(msg))
        fix_missing_tool_responses_openai(messages)
        inject_reasoning_content_openai(messages, model)

        payload: dict[str, Any] = {"model": model, "messages": messages}
        if req.stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop"] = req.stop
        if req.tools:
            payload["tools"] = [self._build_tool_def(t) for t in req.tools]
        if req.tool_choice is not None:
            payload["tool_choice"] = self._build_tool_choice(req.tool_choice)
        if req.thinking is not None and "deepseek" in model.lower():
            payload["thinking"] = req.thinking
        if req.reasoning_effort is not None:
            payload["reasoning_effort"] = req.reasoning_effort
        return payload

    @staticmethod
    def _build_tool_choice(tc: ToolChoiceType) -> Any:
        if isinstance(tc, ToolChoiceAuto):
            return "auto"
        if isinstance(tc, ToolChoiceNone):
            return "none"
        if isinstance(tc, ToolChoiceRequired):
            return "required"
        return {"type": "function", "function": {"name": tc.name}}

    def _build_upstream_messages(self, msg: Message) -> list[dict[str, Any]]:
        if msg.role == Role.TOOL:
            return [self._build_message(msg)]

        tool_results: list[ToolResult] = []
        if isinstance(msg.content, list):
            tool_results = [p for p in msg.content if isinstance(p, ToolResult)]
        if not tool_results:
            return [self._build_message(msg)]

        non_result: str | list[ContentPart]
        if isinstance(msg.content, list):
            non_result = [p for p in msg.content if not isinstance(p, ToolResult)]
        else:
            non_result = msg.content
        built = self._build_message(Message(role=msg.role, content=non_result))

        out: list[dict[str, Any]] = []
        for tr in tool_results:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": tool_result_text(tr.content),
                },
            )
        content = built.get("content")
        if content not in (None, "", []) or built.get("tool_calls"):
            out.append(built)
        return out

    def _build_message(self, msg: Message) -> dict[str, Any]:
        if msg.role == Role.TOOL:
            parts = msg.content if isinstance(msg.content, list) else []
            results = [p for p in parts if isinstance(p, ToolResult)]
            tr = results[0] if results else ToolResult(tool_use_id="", content="")
            return {
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": tool_result_text(tr.content),
            }

        content: Any
        tool_uses: list[ToolUse] = []
        if isinstance(msg.content, str):
            content = msg.content
        else:
            tool_uses = [p for p in msg.content if isinstance(p, ToolUse)]
            non_tool = [
                p
                for p in msg.content
                if not isinstance(p, ToolUse) and not isinstance(p, ToolResult)
            ]
            if not non_tool:
                content = None
            elif all(isinstance(p, TextPart) for p in non_tool):
                texts = [p.text for p in non_tool if isinstance(p, TextPart)]
                content = "".join(texts) if len(texts) > 1 else texts[0]
            else:
                oai_parts: list[dict[str, Any]] = []
                for p in non_tool:
                    if isinstance(p, TextPart):
                        oai_parts.append({"type": "text", "text": p.text})
                    elif isinstance(p, ImagePart):
                        url = p.source.url or ""
                        oai_parts.append({"type": "image_url", "image_url": {"url": url}})
                content = oai_parts

        result: dict[str, Any] = {"role": msg.role.value, "content": content}
        if tool_uses:
            result["tool_calls"] = [
                {
                    "id": tu.id,
                    "type": "function",
                    "function": {"name": tu.name, "arguments": json.dumps(tu.input)},
                }
                for tu in tool_uses
            ]
        if msg.reasoning_content:
            result["reasoning_content"] = msg.reasoning_content
        return result

    @staticmethod
    def _build_tool_def(tool: Tool) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.function.name,
                "description": tool.function.description,
                "parameters": tool.function.parameters,
            },
        }

    # ---- response parsing ----

    def parse_upstream_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")

        parts: list[ContentPart] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(TextPart(text=content))
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parts.append(ToolUse(id=tc.get("id", ""), name=fn.get("name", ""), input=args))

        usage_raw = raw.get("usage") or {}
        usage = Usage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )

        return CanonicalResponse(
            model=raw.get("model", ""),
            content=parts,
            stop_reason=_FINISH_TO_STOP.get(finish_reason or "", finish_reason),
            usage=usage,
            reasoning_content=message.get("reasoning_content"),
        )

    # ---- response emitting ----

    def emit_response(self, resp: CanonicalResponse) -> dict[str, Any]:
        text_parts = [p for p in resp.content if isinstance(p, TextPart)]
        tool_uses = [p for p in resp.content if isinstance(p, ToolUse)]

        if text_parts and not tool_uses and len(resp.content) == len(text_parts):
            content: Any = "".join(p.text for p in text_parts)
        elif not text_parts and tool_uses:
            content = None
        else:
            content = "".join(p.text for p in text_parts) if text_parts else None

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_uses:
            message["tool_calls"] = [
                {
                    "id": tu.id,
                    "type": "function",
                    "function": {"name": tu.name, "arguments": json.dumps(tu.input)},
                }
                for tu in tool_uses
            ]
        if resp.reasoning_content:
            message["reasoning_content"] = resp.reasoning_content
        elif tool_uses and "deepseek" in resp.model.lower():
            message["reasoning_content"] = " "

        finish = _STOP_TO_FINISH.get(resp.stop_reason or "end_turn", "stop")
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": resp.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
                "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        }

    # ---- streaming ----

    def stream_parser(self) -> OpenAIStreamParser:
        return OpenAIStreamParser()

    def stream_emitter(self) -> OpenAIStreamEmitter:
        return OpenAIStreamEmitter()
