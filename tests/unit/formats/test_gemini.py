import json
from pathlib import Path

from janus.canonical.models import (
    CanonicalRequest,
    CanonicalResponse,
    Message,
    Role,
    SystemBlock,
    TextPart,
    Usage,
)
from janus.formats.gemini import GeminiAdapter, _sanitize_parameter_schema

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_antigravity_schema_sanitizer_flattens_pi_browser_union():
    schema = {
        "type": "object",
        "properties": {
            "qa": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "attached": {"type": "boolean", "const": True},
                            "name": {"type": ["string", "null"], "minLength": 1},
                        },
                        "required": ["attached", "missing"],
                        "additionalProperties": False,
                    },
                    {"type": "string"},
                ]
            }
        },
        "additionalProperties": False,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
    }
    cleaned = _sanitize_parameter_schema(schema)
    qa = cleaned["properties"]["qa"]
    assert "anyOf" not in qa
    assert qa["type"] == "object"
    assert qa["properties"]["attached"] == {"type": "boolean", "enum": ["True"]}
    assert qa["properties"]["name"] == {"type": "string"}
    assert qa["required"] == ["attached"]
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned


def test_parse_generate_content():
    raw = json.loads((FIXTURES / "gemini_request.json").read_text())
    req = GeminiAdapter().parse_request(raw)
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].role == Role.USER
    assert isinstance(req.messages[0].content[0], TextPart)
    assert req.messages[0].content[0].text == "Hello"
    assert req.tools[0].function.name == "read"


def test_parse_request_uses_model_field():
    raw = {"model": "openai/gpt-4o", "contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    req = GeminiAdapter().parse_request(raw)
    assert req.model == "openai/gpt-4o"


def test_parse_request_defaults_model_empty():
    raw = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    req = GeminiAdapter().parse_request(raw)
    assert req.model == ""


def test_build_upstream_request():
    req = CanonicalRequest(
        model="gemini-2.0-flash",
        system=[SystemBlock(type="text", text="Be concise")],
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hi")])],
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.0-flash")
    assert payload["system_instruction"]["parts"][0]["text"] == "Be concise"
    assert payload["contents"][0]["parts"][0]["text"] == "hi"


def test_emit_response():
    resp = CanonicalResponse(
        model="gemini-2.0-flash",
        content=[TextPart(type="text", text="Hello!")],
        stop_reason="STOP",
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    out = GeminiAdapter().emit_response(resp)
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "Hello!"
    assert out["candidates"][0]["finishReason"] == "STOP"


def test_parse_upstream_response():
    raw = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Hello!"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
    }
    resp = GeminiAdapter().parse_upstream_response(raw)
    assert isinstance(resp.content[0], TextPart)
    assert resp.content[0].text == "Hello!"
    assert resp.usage.input_tokens == 5


def test_reasoning_part_does_not_crash_build():
    from janus.canonical.models import Reasoning

    req = CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[Message(role=Role.ASSISTANT, content=[Reasoning(text="t"), TextPart(text="hi")])],
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.5-pro")
    assert payload is not None
    text_parts = [p for p in payload["contents"][0]["parts"] if "text" in p]
    assert any(p["text"] == "hi" for p in text_parts)


def test_tool_history_preserves_ids_and_function_names_for_antigravity_claude():
    from janus.canonical.models import ToolResult, ToolUse

    req = CanonicalRequest(
        model="claude-opus-4-6-thinking",
        messages=[
            Message(
                role=Role.ASSISTANT,
                content=[
                    TextPart(text="checking"),
                    ToolUse(id="call_browser_1", name="agent_browser", input={"args": ["open"]}),
                ],
            ),
            Message(
                role=Role.TOOL,
                content=[ToolResult(tool_use_id="call_browser_1", content="page")],
            ),
        ],
    )
    payload = GeminiAdapter().build_upstream_request(req, "claude-opus-4-6-thinking")
    function_call = payload["contents"][0]["parts"][1]["functionCall"]
    function_response = payload["contents"][1]["parts"][0]["functionResponse"]
    assert function_call == {
        "id": "call_browser_1",
        "name": "agent_browser",
        "args": {"args": ["open"]},
    }
    assert function_response == {
        "id": "call_browser_1",
        "name": "agent_browser",
        "response": {"result": "page"},
    }
    assert payload["contents"][1]["role"] == "user"


def test_tool_choice_required_maps_to_gemini_any():
    from janus.canonical.models import ToolChoiceRequired

    req = CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceRequired(),
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.5-pro")
    mode = payload["tool_config"]["function_calling_config"]["mode"]
    assert mode == "ANY"


def test_tool_choice_none_maps_to_gemini_none():
    from janus.canonical.models import ToolChoiceNone

    req = CanonicalRequest(
        model="gemini-2.5-pro",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceNone(),
    )
    payload = GeminiAdapter().build_upstream_request(req, "gemini-2.5-pro")
    assert payload["tool_config"]["function_calling_config"]["mode"] == "NONE"
