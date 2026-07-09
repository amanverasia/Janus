from janus.routing.model_caps import get_model_capabilities


def test_grok4_caps():
    caps = get_model_capabilities("xai", "grok-4")
    assert caps["vision"] is True
    assert caps["reasoning"] is True
    assert caps["thinking_format"] == "openai"
    assert caps["context_window"] == 256_000
    assert caps["search"] is True


def test_grok_code_caps():
    caps = get_model_capabilities("xai", "grok-code-fast-1")
    assert caps["reasoning"] is True
    assert caps["thinking_format"] == "openai"
    assert caps["context_window"] == 256_000


def test_grok3_caps():
    caps = get_model_capabilities("xai", "xai/grok-3")
    assert caps["context_window"] == 131_072
    assert caps["thinking_format"] == "openai"
