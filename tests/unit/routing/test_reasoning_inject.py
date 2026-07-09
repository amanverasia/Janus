from janus.routing.reasoning_inject import inject_reasoning_content


def test_inject_deepseek_all_assistant():
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "assistant", "content": "has", "reasoning_content": "thought"},
        ]
    }
    out = inject_reasoning_content(body, provider="deepseek", model="deepseek-chat")
    assert out["messages"][1]["reasoning_content"] == " "
    assert out["messages"][2]["reasoning_content"] == "thought"
    assert "reasoning_content" not in out["messages"][0]


def test_inject_kimi_only_tool_calls():
    body = {
        "messages": [
            {"role": "assistant", "content": "plain"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "x"}}],
            },
        ]
    }
    out = inject_reasoning_content(body, provider="openai", model="kimi-k2")
    assert "reasoning_content" not in out["messages"][0]
    assert out["messages"][1]["reasoning_content"] == " "


def test_noop_for_openai():
    body = {"messages": [{"role": "assistant", "content": "x"}]}
    out = inject_reasoning_content(body, provider="openai", model="gpt-4o")
    assert "reasoning_content" not in out["messages"][0]


def test_inject_deepseek_v4_pro_max_alias_maps_upstream():
    body = {
        "model": "deepseek-v4-pro-max",
        "messages": [{"role": "assistant", "content": "a"}],
    }
    out = inject_reasoning_content(body, provider="deepseek", model="deepseek-v4-pro-max")
    assert out["model"] == "deepseek-v4-pro"
    assert out["thinking"]["type"] == "enabled"
    assert out["reasoning_effort"] == "max"
