from janus.canonical.models import CanonicalRequest, Message, Role, TextPart
from janus.formats.openai import OpenAIAdapter


def _req(max_tokens: int = 16) -> CanonicalRequest:
    return CanonicalRequest(
        model="x",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        max_tokens=max_tokens,
    )


def test_gpt5_uses_max_completion_tokens():
    payload = OpenAIAdapter().build_upstream_request(_req(), "gpt-5.4")
    assert payload["max_completion_tokens"] == 16
    assert "max_tokens" not in payload


def test_gpt4o_keeps_max_tokens():
    payload = OpenAIAdapter().build_upstream_request(_req(), "gpt-4o")
    assert payload["max_tokens"] == 16
    assert "max_completion_tokens" not in payload


def test_o3_uses_max_completion_tokens():
    payload = OpenAIAdapter().build_upstream_request(_req(), "openai/o3-mini")
    assert payload["max_completion_tokens"] == 16
