from __future__ import annotations

import json
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
from janus.streaming.sse import encode_sse

_ROLE_TO_GEMINI: dict[str, str] = {
    "user": "user",
    "assistant": "model",
}

_GEMINI_TO_ROLE: dict[str, Role] = {
    "user": Role.USER,
    "model": Role.ASSISTANT,
}


class GeminiStreamParser:
    """Parses Gemini SSE chunks into canonical streaming events."""

    def __init__(self) -> None:
        self._started = False
        self._text_started = False
        self._text_index = 0
        self._next_block = 0
        self._tool_indices: dict[str, int] = {}
        self._finished = False
        self._done = False

    def feed(self, line: str) -> list[CanonicalEvent]:
        stripped = line.strip()
        if not stripped:
            return []

        try:
            chunk = json.loads(stripped)
        except json.JSONDecodeError:
            return []

        events: list[CanonicalEvent] = []
        candidates = chunk.get("candidates") or []
        if not candidates:
            return events

        candidate = candidates[0]
        content = candidate.get("content") or {}
        finish_reason = candidate.get("finishReason")
        usage_meta = chunk.get("usageMetadata")
        if self._finished:
            if usage_meta:
                events.append(
                    MessageDelta(
                        usage=Usage(
                            input_tokens=usage_meta.get("promptTokenCount", 0),
                            output_tokens=usage_meta.get("candidatesTokenCount", 0),
                        )
                    )
                )
            return events

        if not self._started:
            self._started = True
            events.append(MessageStart(model=chunk.get("modelVersion", "")))

        parts = content.get("parts") or []
        for part in parts:
            text = part.get("text")
            if text is not None:
                if not self._text_started:
                    self._text_started = True
                    self._text_index = self._next_block
                    self._next_block += 1
                    events.append(TextBlockStart(index=self._text_index))
                events.append(TextDelta(index=self._text_index, text=text))

            fc = part.get("functionCall")
            if fc:
                name = fc.get("name", "")
                tool_id = fc.get("id") or name
                tool_key = str(tool_id or name)
                if tool_key not in self._tool_indices:
                    ci = self._next_block
                    self._next_block += 1
                    self._tool_indices[tool_key] = ci
                    events.append(
                        ToolUseBlockStart(
                            index=ci,
                            id=str(tool_id),
                            name=name,
                        )
                    )
                    args_str = json.dumps(fc.get("args") or {})
                    events.append(InputJsonDelta(index=ci, partial_json=args_str))

        if finish_reason is not None:
            if self._text_started:
                events.append(BlockStop(index=self._text_index))
                self._text_started = False
            for ci in self._tool_indices.values():
                events.append(BlockStop(index=ci))
            self._tool_indices.clear()
            events.append(MessageDelta(stop_reason=finish_reason))
            self._finished = True

        if usage_meta:
            events.append(
                MessageDelta(
                    usage=Usage(
                        input_tokens=usage_meta.get("promptTokenCount", 0),
                        output_tokens=usage_meta.get("candidatesTokenCount", 0),
                    ),
                )
            )

        return events

    def finish(self) -> list[CanonicalEvent]:
        if not self._done:
            self._done = True
            return [MessageStop()]
        return []


class GeminiStreamEmitter:
    """Converts canonical streaming events into Gemini SSE chunks."""

    def __init__(self) -> None:
        self._model = ""
        self._finished = False

    def _make_chunk(
        self,
        parts: list[dict[str, Any]],
        finish_reason: str | None = None,
        usage_metadata: dict[str, Any] | None = None,
    ) -> bytes:
        candidate: dict[str, Any] = {
            "content": {"role": "model", "parts": parts},
        }
        if finish_reason is not None:
            candidate["finishReason"] = finish_reason
        chunk: dict[str, Any] = {"candidates": [candidate]}
        if usage_metadata:
            chunk["usageMetadata"] = usage_metadata
        return encode_sse(chunk)

    def feed(self, event: CanonicalEvent) -> list[bytes]:
        if isinstance(event, MessageStart):
            self._model = event.model
            return []

        if isinstance(event, TextBlockStart):
            return []

        if isinstance(event, ToolUseBlockStart):
            return [self._make_chunk([{"functionCall": {"name": event.name, "args": {}}}])]

        if isinstance(event, TextDelta):
            return [self._make_chunk([{"text": event.text}])]

        if isinstance(event, InputJsonDelta):
            try:
                args = json.loads(event.partial_json)
            except json.JSONDecodeError:
                args = {}
            return [self._make_chunk([{"functionCall": {"name": "", "args": args}}])]

        if isinstance(event, BlockStop):
            return []

        if isinstance(event, MessageDelta):
            finish = event.stop_reason or "STOP"
            usage_metadata: dict[str, Any] | None = None
            if event.usage:
                usage_metadata = {
                    "promptTokenCount": event.usage.input_tokens,
                    "candidatesTokenCount": event.usage.output_tokens,
                }
            return [self._make_chunk([], finish_reason=finish, usage_metadata=usage_metadata)]

        if isinstance(event, MessageStop):
            return []

        return []

    def finish(self) -> list[bytes]:
        if not self._finished:
            self._finished = True
        return []


_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "minLength",
        "maxLength",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "format",
        "multipleOf",
        "uniqueItems",
        "contains",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentSchema",
        "default",
        "examples",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
        "additionalProperties",
        "propertyNames",
        "patternProperties",
        "enumDescriptions",
        "not",
        "dependencies",
        "dependentSchemas",
        "dependentRequired",
        "title",
        "optional",
        "if",
        "then",
        "else",
        "contentMediaType",
        "contentEncoding",
    }
)


def _schema_score(schema: Any) -> int:
    if not isinstance(schema, dict) or schema.get("type") == "null":
        return -1
    if schema.get("type") == "object" or schema.get("properties"):
        return 3
    if schema.get("type") == "array" or schema.get("items"):
        return 2
    return 1 if schema.get("type") else 0


def _sanitize_parameter_schema(schema: Any) -> Any:
    """Port 9router's Antigravity-compatible JSON Schema reduction.

    Cloud Code forwards tool schemas to multiple model backends. Its accepted
    subset is narrower than JSON Schema 2020-12, so complex Pi tool schemas
    must be flattened and stripped before transmission.
    """
    if not isinstance(schema, dict):
        return schema

    working = dict(schema)
    const = working.pop("const", None)
    if const is not None and "enum" not in working:
        working["enum"] = [str(const)]
        working.setdefault("type", "string")

    for union_key in ("anyOf", "oneOf"):
        variants = working.pop(union_key, None)
        if isinstance(variants, list) and variants:
            choices = [item for item in variants if _schema_score(item) >= 0]
            if choices:
                selected = max(choices, key=_schema_score)
                if isinstance(selected, dict):
                    working.update(selected)

    all_of = working.pop("allOf", None)
    if isinstance(all_of, list):
        properties = dict(working.get("properties") or {})
        required = list(working.get("required") or [])
        for item in all_of:
            if not isinstance(item, dict):
                continue
            properties.update(item.get("properties") or {})
            for name in item.get("required") or []:
                if name not in required:
                    required.append(name)
        if properties:
            working["properties"] = properties
        if required:
            working["required"] = required

    schema_type = working.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        working["type"] = non_null[0] if non_null else "string"
    if "properties" in working and not working.get("type"):
        working["type"] = "object"

    out: dict[str, Any] = {}
    for key, value in working.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS or key.startswith("x-"):
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _sanitize_parameter_schema(item) for name, item in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _sanitize_parameter_schema(value)
        elif isinstance(value, dict):
            out[key] = _sanitize_parameter_schema(value)
        elif isinstance(value, list) and key != "enum":
            out[key] = [
                _sanitize_parameter_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        elif key == "enum" and isinstance(value, list):
            out[key] = [str(item) for item in value]
            out.setdefault("type", "string")
        else:
            out[key] = value

    out_properties = out.get("properties")
    out_required = out.get("required")
    if isinstance(out_properties, dict) and isinstance(out_required, list):
        valid = [name for name in out_required if name in out_properties]
        if valid:
            out["required"] = valid
        else:
            out.pop("required", None)
    if not out or (out.get("type") == "object" and not out.get("properties")):
        out = {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of why you are calling this tool",
                }
            },
            "required": ["reason"],
        }
    return out


class GeminiAdapter:
    """Adapter translating between Gemini API and the canonical model."""

    name = "gemini"

    # ---- request parsing ----

    def parse_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        system: list[SystemBlock] = []
        sys_raw = raw.get("system_instruction")
        if isinstance(sys_raw, dict):
            for part in sys_raw.get("parts") or []:
                if isinstance(part, dict) and "text" in part:
                    system.append(SystemBlock(type="text", text=part["text"]))

        messages: list[Message] = []
        for item in raw.get("contents") or []:
            role_str = item.get("role", "user")
            role = _GEMINI_TO_ROLE.get(role_str, Role.USER)
            parts = self._parse_parts(item.get("parts") or [])
            messages.append(Message(role=role, content=parts))

        tools: list[Tool] = []
        for tool_block in raw.get("tools") or []:
            for fd in tool_block.get("functionDeclarations") or []:
                tools.append(
                    Tool(
                        type="function",
                        function=ToolFunction(
                            name=fd.get("name", ""),
                            description=fd.get("description"),
                            parameters=fd.get("parameters") or {},
                        ),
                    )
                )

        gen_config = raw.get("generationConfig") or {}

        return CanonicalRequest(
            model=raw.get("model", ""),
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=self._parse_tool_choice(raw.get("tool_config")),
            max_tokens=gen_config.get("maxOutputTokens"),
            temperature=gen_config.get("temperature"),
            top_p=gen_config.get("topP"),
            stop=list(gen_config["stopSequences"])
            if isinstance(gen_config.get("stopSequences"), list)
            else None,
            stream=bool(raw.get("stream", False)),
        )

    @staticmethod
    def _parse_tool_choice(tc: Any) -> ToolChoiceType | None:
        if not isinstance(tc, dict):
            return None
        fcc = tc.get("function_calling_config") or {}
        mode = str(fcc.get("mode", "")).upper()
        allowed = fcc.get("allowed_function_names") or []
        if mode == "AUTO":
            return ToolChoiceAuto()
        if mode == "NONE":
            return ToolChoiceNone()
        if mode == "ANY":
            if len(allowed) == 1:
                return ToolChoiceSpecific(name=str(allowed[0]))
            return ToolChoiceRequired()
        return None

    @staticmethod
    def _parse_parts(parts: list[Any]) -> list[ContentPart]:
        result: list[ContentPart] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if "text" in part:
                result.append(TextPart(text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"] or {}
                result.append(
                    ToolUse(
                        id=fc.get("name", ""),
                        name=fc.get("name", ""),
                        input=fc.get("args") or {},
                    )
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"] or {}
                response = fr.get("response")
                content_str = json.dumps(response) if response is not None else ""
                result.append(
                    ToolResult(
                        tool_use_id=fr.get("id", ""),
                        content=content_str,
                    )
                )
        return result

    # ---- upstream request building ----

    def build_upstream_request(self, req: CanonicalRequest, model: str) -> dict[str, Any]:
        system_parts = [{"text": b.text} for b in req.system]
        tool_names: dict[str, str] = {}
        for message in req.messages:
            if not isinstance(message.content, list):
                continue
            for part in message.content:
                if isinstance(part, ToolUse) and part.id:
                    tool_names[part.id] = part.name
        contents = [self._build_content(msg, tool_names) for msg in req.messages]

        payload: dict[str, Any] = {
            "model": model,
            "contents": contents,
        }
        if system_parts:
            payload["system_instruction"] = {"parts": system_parts}

        gen_config: dict[str, Any] = {}
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        if req.stop:
            gen_config["stopSequences"] = req.stop
        if gen_config:
            payload["generationConfig"] = gen_config

        if req.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.function.name,
                            "description": t.function.description,
                            "parameters": _sanitize_parameter_schema(t.function.parameters),
                        }
                        for t in req.tools
                    ]
                }
            ]

        if req.tool_choice is not None:
            payload["tool_config"] = self._build_tool_config(req.tool_choice)

        return payload

    @staticmethod
    def _build_tool_config(tc: ToolChoiceType) -> dict[str, Any]:
        if isinstance(tc, ToolChoiceAuto):
            return {"function_calling_config": {"mode": "AUTO"}}
        if isinstance(tc, ToolChoiceNone):
            return {"function_calling_config": {"mode": "NONE"}}
        if isinstance(tc, ToolChoiceSpecific):
            return {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [tc.name],
                }
            }
        return {"function_calling_config": {"mode": "ANY"}}

    @staticmethod
    def _build_content(msg: Message, tool_names: dict[str, str] | None = None) -> dict[str, Any]:
        role = _ROLE_TO_GEMINI.get(msg.role.value, "user")
        if isinstance(msg.content, str):
            return {"role": role, "parts": [{"text": msg.content}]}

        parts: list[dict[str, Any]] = []
        for part in msg.content:
            if isinstance(part, TextPart):
                parts.append({"text": part.text})
            elif isinstance(part, ToolUse):
                # Cloud Code may route Gemini-shaped requests to Claude. The
                # bridge requires a stable id to construct Anthropic tool_use;
                # omitting it causes intermittent `tool_use.id: Field required`
                # once a Pi conversation contains tool history.
                parts.append(
                    {
                        "functionCall": {
                            "id": part.id,
                            "name": part.name,
                            "args": part.input,
                        }
                    }
                )
            elif isinstance(part, ToolResult):
                content_str = tool_result_text(part.content)
                try:
                    response = json.loads(content_str)
                except (json.JSONDecodeError, TypeError):
                    response = {"result": content_str}
                name = (tool_names or {}).get(part.tool_use_id) or part.tool_use_id or "tool"
                parts.append(
                    {
                        "functionResponse": {
                            "id": part.tool_use_id,
                            "name": name,
                            "response": response,
                        }
                    }
                )
        return {"role": role, "parts": parts}

    # ---- response parsing ----

    def parse_upstream_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        candidates = raw.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        content = candidate.get("content") or {}
        finish_reason = candidate.get("finishReason")

        parts: list[ContentPart] = []
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if "text" in part:
                parts.append(TextPart(text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"] or {}
                parts.append(
                    ToolUse(
                        id=fc.get("id") or fc.get("name", ""),
                        name=fc.get("name", ""),
                        input=fc.get("args") or {},
                    )
                )

        usage_meta = raw.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=usage_meta.get("promptTokenCount", 0),
            output_tokens=usage_meta.get("candidatesTokenCount", 0),
        )

        return CanonicalResponse(
            model=raw.get("modelVersion", ""),
            content=parts,
            stop_reason=finish_reason,
            usage=usage,
        )

    # ---- response emitting ----

    def emit_response(self, resp: CanonicalResponse) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for part in resp.content:
            if isinstance(part, TextPart):
                parts.append({"text": part.text})
            elif isinstance(part, ToolUse):
                parts.append(
                    {
                        "functionCall": {
                            "id": part.id,
                            "name": part.name,
                            "args": part.input,
                        }
                    }
                )

        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": parts},
                    "finishReason": resp.stop_reason or "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": resp.usage.input_tokens,
                "candidatesTokenCount": resp.usage.output_tokens,
                "totalTokenCount": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        }

    # ---- streaming ----

    def stream_parser(self) -> GeminiStreamParser:
        return GeminiStreamParser()

    def stream_emitter(self) -> GeminiStreamEmitter:
        return GeminiStreamEmitter()
