from janus.routing.model_aliases import resolve_model_alias
from janus.routing.reasoning_inject import inject_reasoning_content
from janus.routing.thinking import apply_thinking_to_payload


def test_deepseek_v4_pro_max_alias():
    model, intent = resolve_model_alias("deepseek", "deepseek-v4-pro-max")
    assert model == "deepseek-v4-pro"
    assert intent == {"mode": "level", "level": "max"}


def test_deepseek_v4_pro_none_alias():
    model, intent = resolve_model_alias("deepseek", "deepseek-v4-pro-none")
    assert model == "deepseek-v4-pro"
    assert intent == {"mode": "none"}


def test_unknown_model_passthrough():
    model, intent = resolve_model_alias("deepseek", "deepseek-v4-pro")
    assert model == "deepseek-v4-pro"
    assert intent is None


def test_inject_applies_v4_pro_max_fields():
    body = {
        "model": "deepseek-v4-pro-max",
        "messages": [{"role": "assistant", "content": "prev"}],
    }
    out = inject_reasoning_content(body, provider="deepseek", model="deepseek-v4-pro-max")
    assert out["model"] == "deepseek-v4-pro"
    assert out["thinking"] == {"type": "enabled"}
    assert out["reasoning_effort"] == "max"
    assert out["messages"][0]["reasoning_content"] == " "


def test_inject_applies_v4_pro_none_fields():
    body = {
        "model": "deepseek-v4-pro-none",
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "high",
    }
    out = inject_reasoning_content(body, provider="deepseek", model="deepseek-v4-pro-none")
    assert out["model"] == "deepseek-v4-pro"
    assert out["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in out


def test_alias_intent_drives_thinking_payload():
    payload: dict = {"model": "deepseek-v4-pro", "messages": []}
    _, intent = resolve_model_alias("deepseek", "deepseek-v4-pro-max")
    apply_thinking_to_payload(
        payload,
        target_format="openai",
        model="deepseek-v4-pro",
        caps={"reasoning": True, "thinking_format": "deepseek"},
        intent=intent,
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "max"
