from janus.canonical.models import (
    CanonicalRequest,
    ImagePart,
    ImageSource,
    Message,
    Role,
    TextPart,
)
from janus.routing.capabilities import (
    detect_required_capabilities,
    reorder_combo_by_capabilities,
)


def test_detect_vision_from_image_part():
    req = CanonicalRequest(
        model="c",
        messages=[
            Message(role=Role.USER, content=[ImagePart(source=ImageSource(type="url", url="x"))])
        ],
    )
    assert "vision" in detect_required_capabilities(req)


def test_detect_none_for_text():
    req = CanonicalRequest(
        model="c", messages=[Message(role=Role.USER, content=[TextPart(text="hi")])]
    )
    assert detect_required_capabilities(req) == frozenset()


def test_reorder_prioritizes_vision_capable(monkeypatch):
    import janus.routing.capabilities as cap

    caps = {
        "openai": {"vision": True, "tool_use": True},
        "groq": {"vision": False, "tool_use": True},
    }
    monkeypatch.setattr(cap, "get_provider_capabilities", lambda p: caps.get(p, {"tool_use": True}))
    models = ["groq/x", "openai/y"]
    out = reorder_combo_by_capabilities(models, frozenset({"vision"}))
    assert out[0] == "openai/y"
    assert set(out) == set(models)  # nothing dropped


def test_reorder_noop_without_required():
    models = ["groq/x", "openai/y"]
    assert reorder_combo_by_capabilities(models, frozenset()) == models
