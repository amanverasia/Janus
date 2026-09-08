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


def _parse(payload: dict) -> CanonicalRequest:
    return OpenAIAdapter().parse_request(payload)


def test_parse_accepts_max_tokens():
    req = _parse({"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 123})
    assert req.max_tokens == 123


def test_parse_accepts_max_completion_tokens():
    req = _parse(
        {
            "model": "gpt-6-astra",
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": 128000,
        }
    )
    assert req.max_tokens == 128000


def test_parse_prefers_max_tokens_when_both_present():
    req = _parse(
        {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "max_completion_tokens": 128000,
        }
    )
    assert req.max_tokens == 10


def test_parse_max_completion_absent_gives_none():
    req = _parse({"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    assert req.max_tokens is None
