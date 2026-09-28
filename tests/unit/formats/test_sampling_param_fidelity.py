from __future__ import annotations

import logging

import pytest

from janus.canonical.models import CanonicalRequest, Message, Role, TextPart
from janus.canonical.params import SAMPLING_PARAMS
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.gemini import GeminiAdapter
from janus.formats.ollama import OllamaAdapter
from janus.formats.openai import OpenAIAdapter
from janus.formats.openai_responses import OpenAIResponsesAdapter

_SAMPLING_BODY: dict[str, object] = {
    "seed": 42,
    "n": 2,
    "presence_penalty": 0.5,
    "frequency_penalty": -0.25,
    "logit_bias": {"50256": -100},
    "parallel_tool_calls": False,
    "logprobs": True,
    "top_logprobs": 5,
}


def _client_raw(**extra: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "model": "test/test-m1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    raw.update(_SAMPLING_BODY)
    raw.update(extra)
    return raw


def _canonical(**params: object) -> CanonicalRequest:
    base: dict[str, object] = {
        "model": "x",
        "messages": [Message(role=Role.USER, content=[TextPart(text="hi")])],
    }
    base.update(params)
    return CanonicalRequest(**base)


def test_openai_parse_keeps_every_sampling_param() -> None:
    req = OpenAIAdapter().parse_request(_client_raw())
    assert req.seed == 42
    assert req.n == 2
    assert req.presence_penalty == 0.5
    assert req.frequency_penalty == -0.25
    assert req.logit_bias == {"50256": -100}
    assert req.parallel_tool_calls is False
    assert req.logprobs is True
    assert req.top_logprobs == 5


def test_openai_parse_defaults_sampling_params_to_none() -> None:
    req = OpenAIAdapter().parse_request(
        {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    )
    for name in SAMPLING_PARAMS:
        assert getattr(req, name) is None


def test_openai_parse_accepts_string_stop() -> None:
    req = OpenAIAdapter().parse_request(
        {"model": "x", "messages": [{"role": "user", "content": "hi"}], "stop": "END"}
    )
    assert req.stop == ["END"]


def test_openai_parse_ignores_non_dict_logit_bias() -> None:
    req = OpenAIAdapter().parse_request(
        {"model": "x", "messages": [{"role": "user", "content": "hi"}], "logit_bias": "bad"}
    )
    assert req.logit_bias is None


def test_openai_build_forwards_every_sampling_param() -> None:
    payload = OpenAIAdapter().build_upstream_request(_canonical(**_SAMPLING_BODY), "m1")
    for name, value in _SAMPLING_BODY.items():
        assert payload[name] == value


def test_openai_build_omits_unset_sampling_params() -> None:
    payload = OpenAIAdapter().build_upstream_request(_canonical(), "m1")
    for name in SAMPLING_PARAMS:
        assert name not in payload


def test_openai_parse_build_roundtrip_is_lossless() -> None:
    payload = OpenAIAdapter().build_upstream_request(
        OpenAIAdapter().parse_request(_client_raw()), "m1"
    )
    for name, value in _SAMPLING_BODY.items():
        assert payload[name] == value


def test_openai_build_max_completion_tokens_reaches_reasoning_models() -> None:
    req = OpenAIAdapter().parse_request(
        {
            "model": "openai/o3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": 4096,
        }
    )
    payload = OpenAIAdapter().build_upstream_request(req, "openai/o3")
    assert payload["max_completion_tokens"] == 4096


def test_anthropic_build_drops_unsupported_sampling_params(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="janus.canonical.params"):
        payload = AnthropicAdapter().build_upstream_request(_canonical(**_SAMPLING_BODY), "m1")
    for name in SAMPLING_PARAMS:
        assert name not in payload
    dropped = caplog.records[-1].getMessage()
    for name in SAMPLING_PARAMS:
        assert name in dropped


def test_gemini_build_maps_seed_drops_rest(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="janus.canonical.params"):
        payload = GeminiAdapter().build_upstream_request(_canonical(**_SAMPLING_BODY), "m1")
    assert payload["generationConfig"]["seed"] == 42
    assert "presence_penalty" in caplog.records[-1].getMessage()
    gen_config = payload["generationConfig"]
    assert "n" not in gen_config and "presence_penalty" not in gen_config


def test_ollama_build_maps_seed_drops_rest(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="janus.canonical.params"):
        payload = OllamaAdapter().build_upstream_request(_canonical(**_SAMPLING_BODY), "m1")
    assert payload["options"]["seed"] == 42
    assert "presence_penalty" in caplog.records[-1].getMessage()
    assert "presence_penalty" not in payload["options"]


def test_responses_build_maps_parallel_tool_calls_drops_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="janus.canonical.params"):
        payload = OpenAIResponsesAdapter().build_upstream_request(
            _canonical(**_SAMPLING_BODY), "m1"
        )
    assert payload["parallel_tool_calls"] is False
    assert "seed" in caplog.records[-1].getMessage()
    assert "seed" not in payload


def test_no_drop_log_when_params_absent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="janus.canonical.params"):
        AnthropicAdapter().build_upstream_request(_canonical(), "m1")
    assert not [r for r in caplog.records if "Dropping" in r.getMessage()]
