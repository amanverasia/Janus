from janus.routing.claude_normalize import normalize_claude_passthrough


def test_adaptive_thinking_downgraded_for_haiku() -> None:
    body = {
        "model": "claude-haiku-4-5-20251001",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = normalize_claude_passthrough(body, "claude-haiku-4-5-20251001")
    assert out["thinking"]["type"] == "enabled"
    assert "effort" not in out.get("output_config", {})


def test_system_role_messages_moved_to_system() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "hi"},
        ]
    }
    out = normalize_claude_passthrough(body, "claude-sonnet-4-20250514")
    assert out["system"] == [{"type": "text", "text": "Be concise"}]
    assert len(out["messages"]) == 1
    assert out["messages"][0]["role"] == "user"


def test_invalid_thinking_signature_stripped_and_placeholder_added() -> None:
    body = {
        "thinking": {"type": "enabled", "budget_tokens": 1000},
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "plan",
                        "signature": '{"openai": true}',
                    },
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                ],
            }
        ],
    }
    out = normalize_claude_passthrough(body, "claude-sonnet-4-20250514")
    blocks = out["messages"][0]["content"]
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["signature"] == "janus-placeholder"
    assert any(b.get("type") == "tool_use" for b in blocks)
