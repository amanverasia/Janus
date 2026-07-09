from janus.canonical.models import (
    CanonicalRequest,
    ContentPart,  # noqa: F401
    Message,
    Reasoning,
    Role,
    SystemBlock,
    TextPart,
    Tool,
    ToolFunction,
    ToolResult,
    ToolUse,
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
        tools=[
            Tool(
                type="function",
                function=ToolFunction(name="read", parameters={"type": "object"}),
            )
        ],
    )
    assert req.tools[0].function.name == "read"


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
