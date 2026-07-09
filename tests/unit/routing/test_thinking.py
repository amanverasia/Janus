from janus.canonical.models import CanonicalRequest, Message, Role, TextPart
from janus.routing.thinking import (
    apply_thinking_to_payload,
    extract_thinking,
    parse_thinking_suffix,
    resolve_thinking_intent,
    strip_thinking_suffix,
)


def test_strip_and_parse_suffix():
    assert strip_thinking_suffix("gpt-4o(high)") == "gpt-4o"
    clean, ov = parse_thinking_suffix("claude-sonnet-4(8192)")
    assert clean == "claude-sonnet-4"
    assert ov == {"mode": "budget", "budget": 8192}
    clean, ov = parse_thinking_suffix("o3(none)")
    assert ov == {"mode": "none"}


def test_extract_thinking_from_request():
    req = CanonicalRequest(
        model="m",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        reasoning_effort="high",
    )
    assert extract_thinking(req) == {"mode": "level", "level": "high"}

    req2 = req.model_copy(
        update={
            "reasoning_effort": None,
            "thinking": {"type": "enabled", "budget_tokens": 2000},
        }
    )
    assert extract_thinking(req2) == {"mode": "budget", "budget": 2000}


def test_resolve_thinking_intent_cleans_model():
    req = CanonicalRequest(
        model="openai/gpt-4o(high)",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
    )
    cleaned, intent = resolve_thinking_intent(req)
    assert cleaned.model == "openai/gpt-4o"
    assert intent == {"mode": "level", "level": "high"}


def test_apply_openai_thinking():
    payload: dict = {"model": "o3(high)", "messages": []}
    apply_thinking_to_payload(
        payload,
        target_format="openai",
        model="o3(high)",
        caps={"reasoning": True, "thinking_format": "openai"},
        intent={"mode": "level", "level": "high"},
    )
    assert payload["reasoning_effort"] == "high"
    assert payload["model"] == "o3"


def test_apply_claude_budget_thinking():
    payload: dict = {"model": "claude", "messages": []}
    apply_thinking_to_payload(
        payload,
        target_format="anthropic",
        model="claude",
        caps={"reasoning": True, "thinking_format": "claude-budget"},
        intent={"mode": "budget", "budget": 2000},
    )
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 2000}


def test_apply_deepseek_thinking():
    payload: dict = {"model": "deepseek-r1", "messages": []}
    apply_thinking_to_payload(
        payload,
        target_format="openai",
        model="deepseek-r1",
        caps={"reasoning": True, "thinking_format": "deepseek"},
        intent={"mode": "level", "level": "low"},
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_strip_when_no_reasoning():
    payload: dict = {"model": "x", "reasoning_effort": "high", "thinking": {"type": "enabled"}}
    apply_thinking_to_payload(
        payload,
        target_format="openai",
        model="x",
        caps={"reasoning": False},
        intent={"mode": "level", "level": "high"},
    )
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload
