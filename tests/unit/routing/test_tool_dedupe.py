from janus.routing.tool_dedupe import dedupe_tools


def test_dedupe_strips_web_search_when_exa_present() -> None:
    tools = [
        {"name": "WebSearch", "input_schema": {"type": "object"}},
        {"name": "mcp__exa__web_search_exa", "input_schema": {"type": "object"}},
        {"name": "Bash", "input_schema": {"type": "object"}},
    ]
    out, stripped = dedupe_tools(tools)
    assert stripped == ["WebSearch"]
    assert [t["name"] for t in out] == ["mcp__exa__web_search_exa", "Bash"]


def test_dedupe_openai_function_shape() -> None:
    tools = [
        {"type": "function", "function": {"name": "WebFetch"}},
        {"type": "function", "function": {"name": "mcp__tavily__tavily_search"}},
    ]
    out, stripped = dedupe_tools(tools)
    assert stripped == ["WebFetch"]
    assert len(out) == 1


def test_dedupe_noop_without_trigger() -> None:
    tools = [{"name": "WebSearch"}, {"name": "Bash"}]
    out, stripped = dedupe_tools(tools)
    assert stripped == []
    assert out == tools
