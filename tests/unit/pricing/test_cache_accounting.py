"""Cross-format cache-token accounting (issue #105).

Invariant under test: across every supported upstream format, the parsed
canonical ``Usage.input_tokens`` is the *total* prompt input, and
``cache_creation_input_tokens`` / ``cache_read_input_tokens`` are subsets of
it. ``compute_cost`` must therefore bill the uncached remainder
``max(input - cache_creation - cache_read, 0)`` at the full input rate and the
cache subsets at their own rates — never the full input rate on top of cache.
"""

from janus.formats.anthropic import AnthropicAdapter
from janus.formats.gemini import GeminiAdapter
from janus.formats.openai import OpenAIAdapter
from janus.formats.openai_responses import OpenAIResponsesAdapter
from janus.pricing.calculator import compute_cost
from janus.pricing.registry import PricingRegistry


def _flat_registry() -> PricingRegistry:
    # Every category priced at 1.0 / Mtok so cost == (sum of billed tokens) / 1M.
    return PricingRegistry(
        {
            "flat-model": {
                "input_per_mtok": 1.0,
                "output_per_mtok": 1.0,
                "cache_creation_per_mtok": 1.0,
                "cache_read_per_mtok": 1.0,
            }
        }
    )


def _anthropic_response(usage: dict) -> dict:
    return {
        "model": "claude-sonnet-4-20250514",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": usage,
    }


def _openai_chat_response(usage: dict) -> dict:
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "model": "flat-model",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": usage,
    }


def _openai_responses_response(usage: dict) -> dict:
    return {
        "id": "resp_x",
        "object": "response",
        "status": "completed",
        "model": "flat-model",
        "output": [],
        "usage": usage,
    }


def _gemini_response(usage_meta: dict) -> dict:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": usage_meta,
    }


# --- Anthropic: disjoint upstream fields are normalized to total input --------


def test_anthropic_normalizes_disjoint_usage_to_total_input():
    raw = _anthropic_response(
        {
            "input_tokens": 600,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 100,
            "output_tokens": 200,
        }
    )
    usage = AnthropicAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000  # 600 uncached + 300 creation + 100 read
    assert usage.cache_creation_input_tokens == 300
    assert usage.cache_read_input_tokens == 100
    assert usage.output_tokens == 200


def test_anthropic_cache_read_only_normalizes_to_total():
    raw = _anthropic_response(
        {"input_tokens": 900, "cache_read_input_tokens": 100, "output_tokens": 200}
    )
    usage = AnthropicAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 100
    assert usage.cache_creation_input_tokens == 0


def test_anthropic_missing_cache_fields_default_to_zero():
    raw = _anthropic_response({"input_tokens": 1000, "output_tokens": 200})
    usage = AnthropicAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0


# --- OpenAI Chat Completions: prompt_tokens is total, cached_tokens is subset --


def test_openai_chat_parses_cached_tokens_subset():
    raw = _openai_chat_response(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 100},
        }
    )
    usage = OpenAIAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 100
    assert usage.output_tokens == 200


def test_openai_chat_missing_cache_details_default_to_zero():
    raw = _openai_chat_response({"prompt_tokens": 1000, "completion_tokens": 200})
    usage = OpenAIAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 0


# --- OpenAI Responses: input_tokens is total, cached_tokens is subset ----------


def test_openai_responses_parses_cached_tokens_subset():
    raw = _openai_responses_response(
        {"input_tokens": 1000, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 100}}
    )
    usage = OpenAIResponsesAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 100
    assert usage.output_tokens == 200


# --- Gemini: promptTokenCount is total, cachedContentTokenCount is subset ------


def test_gemini_parses_cached_content_subset():
    raw = _gemini_response(
        {"promptTokenCount": 1000, "candidatesTokenCount": 200, "cachedContentTokenCount": 100}
    )
    usage = GeminiAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 100
    assert usage.output_tokens == 200


def test_gemini_missing_cached_content_defaults_to_zero():
    raw = _gemini_response({"promptTokenCount": 1000, "candidatesTokenCount": 200})
    usage = GeminiAdapter().parse_upstream_response(raw).usage
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 0


# --- Cross-format cost convergence -------------------------------------------


def test_cache_read_costs_converge_across_formats():
    """A cached-read payload with the same effective totals must cost the same
    regardless of upstream wire format, and must be cheaper than billing cached
    tokens at the full input rate."""
    reg = _flat_registry()
    expected = (900 + 200 + 100) / 1_000_000  # uncached + output + cache_read

    anthropic = (
        AnthropicAdapter()
        .parse_upstream_response(
            _anthropic_response(
                {"input_tokens": 900, "cache_read_input_tokens": 100, "output_tokens": 200}
            )
        )
        .usage
    )
    chat = (
        OpenAIAdapter()
        .parse_upstream_response(
            _openai_chat_response(
                {
                    "prompt_tokens": 1000,
                    "completion_tokens": 200,
                    "prompt_tokens_details": {"cached_tokens": 100},
                }
            )
        )
        .usage
    )
    responses = (
        OpenAIResponsesAdapter()
        .parse_upstream_response(
            _openai_responses_response(
                {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "input_tokens_details": {"cached_tokens": 100},
                }
            )
        )
        .usage
    )
    gemini = (
        GeminiAdapter()
        .parse_upstream_response(
            _gemini_response(
                {
                    "promptTokenCount": 1000,
                    "candidatesTokenCount": 200,
                    "cachedContentTokenCount": 100,
                }
            )
        )
        .usage
    )

    for usage in (anthropic, chat, responses, gemini):
        assert abs(compute_cost(usage, "flat-model", reg) - expected) < 1e-12

    # Double-billing guard: charging full input + cache_read would be strictly larger.
    naive = (1000 + 200 + 100) / 1_000_000
    assert expected < naive


def test_anthropic_cache_creation_costs_converge():
    reg = _flat_registry()
    usage = (
        AnthropicAdapter()
        .parse_upstream_response(
            _anthropic_response(
                {"input_tokens": 700, "cache_creation_input_tokens": 300, "output_tokens": 200}
            )
        )
        .usage
    )
    assert usage.input_tokens == 1000
    expected = (700 + 300 + 200) / 1_000_000  # uncached + cache_creation + output
    assert abs(compute_cost(usage, "flat-model", reg) - expected) < 1e-12


def test_malformed_inconsistent_payload_does_not_overcharge():
    """An upstream that lies (cached subset exceeding the reported total input)
    must never produce a negative uncached bill. The OpenAI Chat path takes
    prompt_tokens as-is, so an inflated cached_tokens exercises the clamp."""
    reg = _flat_registry()
    usage = (
        OpenAIAdapter()
        .parse_upstream_response(
            _openai_chat_response(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 0,
                    "prompt_tokens_details": {"cached_tokens": 200},
                }
            )
        )
        .usage
    )
    assert usage.input_tokens == 100
    assert usage.cache_read_input_tokens == 200
    cost = compute_cost(usage, "flat-model", reg)
    assert cost >= 0
    # uncached clamped to 0; only the cached subset is billed: 200 / 1M.
    assert abs(cost - 200 / 1_000_000) < 1e-12


def test_anthropic_inconsistent_raw_usage_is_self_consistent_after_normalization():
    """Anthropic normalization folds cache subsets into the total, so the
    uncached remainder is always the raw non-cached input — never negative."""
    reg = _flat_registry()
    usage = (
        AnthropicAdapter()
        .parse_upstream_response(
            _anthropic_response(
                {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 50,
                    "output_tokens": 0,
                }
            )
        )
        .usage
    )
    assert usage.input_tokens == 350  # 100 + 200 + 50
    cost = compute_cost(usage, "flat-model", reg)
    assert cost >= 0
    # raw non-cached input (100) billed at input rate + cache subsets at theirs.
    assert abs(cost - 350 / 1_000_000) < 1e-12
