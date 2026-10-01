from __future__ import annotations

import time

import pytest

from janus.canonical.models import CanonicalRequest, Message, Usage
from janus.routing.prompt_cache import (
    CachedResponse,
    PromptCache,
    compute_cache_key,
    is_cacheable_request,
    prompt_cache_clear,
    prompt_cache_get,
    prompt_cache_put,
)


def _req(**overrides) -> CanonicalRequest:
    base: dict = {
        "model": "m1",
        "messages": [Message(role="user", content="hi")],
    }
    base.update(overrides)
    return CanonicalRequest(**base)


def _entry(payload: dict | None = None) -> CachedResponse:
    return CachedResponse(
        payload=payload if payload is not None else {"ok": True},
        model="m1",
        provider_id="p1",
        account_id=None,
        usage=Usage(input_tokens=1, output_tokens=2),
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    prompt_cache_clear()
    yield
    prompt_cache_clear()


class TestCacheablePredicate:
    def test_explicit_zero_temperature_is_cacheable(self):
        assert is_cacheable_request(_req(temperature=0))

    def test_zero_float_temperature_is_cacheable(self):
        assert is_cacheable_request(_req(temperature=0.0))

    def test_absent_temperature_is_not_cacheable(self):
        assert not is_cacheable_request(_req())

    def test_nonzero_temperature_is_not_cacheable(self):
        assert not is_cacheable_request(_req(temperature=0.7))

    def test_narrowed_top_p_blocks_zero_temperature(self):
        assert not is_cacheable_request(_req(temperature=0, top_p=0.9))

    def test_full_top_p_keeps_zero_temperature_cacheable(self):
        assert is_cacheable_request(_req(temperature=0, top_p=1.0))

    def test_pinned_seed_is_cacheable(self):
        assert is_cacheable_request(_req(seed=42))

    def test_pinned_seed_with_narrowed_top_p_is_cacheable(self):
        assert is_cacheable_request(_req(seed=42, top_p=0.9, temperature=0.5))

    def test_streaming_is_never_cacheable(self):
        assert not is_cacheable_request(_req(stream=True, temperature=0))
        assert not is_cacheable_request(_req(stream=True, seed=1))


class TestCacheKey:
    def test_identical_requests_share_a_key(self):
        a = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0),
        )
        b = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0),
        )
        assert a == b

    def test_absent_temperature_differs_from_zero(self):
        absent = compute_cache_key(client_key_id=1, client_format="openai", canonical_req=_req())
        zero = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        assert absent != zero

    def test_client_key_id_is_in_the_key(self):
        a = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        b = compute_cache_key(
            client_key_id=2, client_format="openai", canonical_req=_req(temperature=0)
        )
        assert a != b

    def test_none_client_key_id_differs_from_any_key(self):
        a = compute_cache_key(
            client_key_id=None, client_format="openai", canonical_req=_req(temperature=0)
        )
        b = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        assert a != b

    def test_client_format_is_in_the_key(self):
        a = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        b = compute_cache_key(
            client_key_id=1, client_format="anthropic", canonical_req=_req(temperature=0)
        )
        assert a != b

    def test_message_content_is_in_the_key(self):
        a = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0, messages=[Message(role="user", content="hi")]),
        )
        b = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0, messages=[Message(role="user", content="ho")]),
        )
        assert a != b

    def test_output_shaping_params_are_in_the_key(self):
        base = _req(temperature=0)
        variants = [
            _req(temperature=0, max_tokens=10),
            _req(temperature=0, n=2),
            _req(temperature=0, presence_penalty=0.5),
            _req(temperature=0, frequency_penalty=0.5),
            _req(temperature=0, logit_bias={"50256": -100}),
            _req(temperature=0, stop=["END"]),
            _req(temperature=0, seed=7),
            _req(temperature=0, logprobs=True),
            _req(temperature=0, top_logprobs=3),
            _req(temperature=0, parallel_tool_calls=False),
        ]
        keys = {
            compute_cache_key(client_key_id=1, client_format="openai", canonical_req=v)
            for v in variants
        }
        base_key = compute_cache_key(client_key_id=1, client_format="openai", canonical_req=base)
        assert base_key not in keys
        assert len(keys) == len(variants)

    def test_thinking_intent_is_in_the_key(self):
        a = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0),
            thinking_intent={"type": "enabled", "budget_tokens": 1024},
        )
        b = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        assert a != b

    def test_client_tool_is_in_the_key(self):
        a = compute_cache_key(
            client_key_id=1,
            client_format="openai",
            canonical_req=_req(temperature=0),
            client_tool="claude-code",
        )
        b = compute_cache_key(
            client_key_id=1, client_format="openai", canonical_req=_req(temperature=0)
        )
        assert a != b


class TestPromptCache:
    def test_put_then_get_roundtrip(self):
        cache = PromptCache()
        cache.put("k", _entry(), max_entries=4)
        got = cache.get("k", ttl_s=60)
        assert got is not None
        assert got.payload == {"ok": True}
        assert got.usage.output_tokens == 2

    def test_missing_key_returns_none(self):
        cache = PromptCache()
        assert cache.get("nope", ttl_s=60) is None

    def test_expired_entry_is_dropped(self):
        cache = PromptCache()
        entry = _entry()
        cache.put("k", entry, max_entries=4)
        entry.created = time.monotonic() - 120
        assert cache.get("k", ttl_s=60) is None

    def test_ttl_get_is_lru(self):
        cache = PromptCache()
        cache.put("a", _entry({"n": 1}), max_entries=3)
        cache.put("b", _entry({"n": 2}), max_entries=3)
        assert cache.get("a", ttl_s=60) is not None
        cache.put("c", _entry({"n": 3}), max_entries=3)
        cache.put("d", _entry({"n": 4}), max_entries=3)
        assert cache.get("b", ttl_s=60) is None
        assert cache.get("a", ttl_s=60) is not None
        assert cache.get("c", ttl_s=60) is not None
        assert cache.get("d", ttl_s=60) is not None

    def test_zero_max_entries_disables_put(self):
        cache = PromptCache()
        cache.put("k", _entry(), max_entries=0)
        assert len(cache) == 0

    def test_clear(self):
        cache = PromptCache()
        cache.put("k", _entry(), max_entries=4)
        cache.clear()
        assert len(cache) == 0


class TestBoundedPut:
    def test_oversized_payload_is_not_stored(self):
        big = {"text": "x" * 512}
        prompt_cache_put("k", _entry(big), max_entries=4, max_body_bytes=16)
        assert prompt_cache_get("k", ttl_s=60) is None

    def test_zero_budget_disables_put(self):
        prompt_cache_put("k", _entry(), max_entries=4, max_body_bytes=0)
        assert prompt_cache_get("k", ttl_s=60) is None

    def test_entry_evicted_beyond_max_entries(self):
        for i in range(3):
            prompt_cache_put(f"k{i}", _entry({"i": i}), max_entries=2, max_body_bytes=10_000)
        assert prompt_cache_get("k0", ttl_s=60) is None
        assert prompt_cache_get("k1", ttl_s=60) is not None
        assert prompt_cache_get("k2", ttl_s=60) is not None
