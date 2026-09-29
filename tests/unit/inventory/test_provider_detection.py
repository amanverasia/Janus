from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from janus.inventory import provider_detection
from janus.inventory.provider_detection import (
    accepts_any_key,
    detectable_provider_ids,
    find_authenticating_provider,
    resolve_provider_for_key,
)


@pytest.fixture(autouse=True)
def _clear_accepts_any_key_cache():
    provider_detection._ACCEPTS_ANY_KEY.clear()
    yield
    provider_detection._ACCEPTS_ANY_KEY.clear()


def test_detectable_provider_ids_excludes_meta_providers():
    ids = detectable_provider_ids()
    assert "custom" not in ids
    assert "unidentified" not in ids
    assert "openrouter" not in ids
    assert "openai" in ids


def test_detectable_provider_ids_honors_exclude():
    ids = detectable_provider_ids("openai")
    assert "openai" not in ids
    assert "anthropic" in ids


@pytest.mark.asyncio
async def test_find_authenticating_provider_returns_first_valid():
    async def fake_validate(key_value: str, provider_id: str, metadata, *, skip_probe: bool):
        del metadata, skip_probe
        return {"is_valid": key_value == "secret" and provider_id == "groq"}

    with patch(
        "janus.inventory.provider_detection.validate_key",
        new=AsyncMock(side_effect=fake_validate),
    ):
        result = await find_authenticating_provider("secret", ["openai", "groq", "anthropic"])

    assert result == "groq"


@pytest.mark.asyncio
async def test_find_authenticating_provider_prefers_lower_rank():
    async def fake_validate(key_value: str, provider_id: str, metadata, *, skip_probe: bool):
        del metadata, skip_probe
        return {"is_valid": key_value == "secret" and provider_id in {"openai", "groq"}}

    with patch(
        "janus.inventory.provider_detection.validate_key",
        new=AsyncMock(side_effect=fake_validate),
    ):
        result = await find_authenticating_provider("secret", ["openai", "groq"])

    assert result == "openai"


@pytest.mark.asyncio
async def test_resolve_provider_for_key_manual_choice_skips_detection():
    with patch(
        "janus.inventory.provider_detection.find_authenticating_provider",
        new=AsyncMock(),
    ) as detect:
        provider_id, metadata = await resolve_provider_for_key(
            "sk-proj-test",
            chosen_provider="anthropic",
        )

    detect.assert_not_awaited()
    assert provider_id == "anthropic"
    assert metadata is None


@pytest.mark.asyncio
async def test_resolve_provider_for_key_auto_uses_detection():
    with patch(
        "janus.inventory.provider_detection.find_authenticating_provider",
        new=AsyncMock(return_value="groq"),
    ):
        provider_id, metadata = await resolve_provider_for_key("gsk_test", chosen_provider="auto")

    assert provider_id == "groq"
    assert metadata is None


@pytest.mark.asyncio
async def test_find_authenticating_provider_skips_providers_that_accept_any_key():
    async def fake_validate(key_value: str, provider_id: str, metadata, *, skip_probe: bool):
        del metadata, skip_probe
        if provider_id == "venice":
            return {"is_valid": True}
        return {"is_valid": key_value == "secret" and provider_id == "moonshot"}

    validate = AsyncMock(side_effect=fake_validate)
    with patch("janus.inventory.provider_detection.validate_key", new=validate):
        first = await find_authenticating_provider("secret", ["groq", "venice", "moonshot"])
        unknown = await find_authenticating_provider("other", ["venice"])

    assert first == "moonshot"
    assert unknown is None
    venice_calls = [call for call in validate.await_args_list if call.args[1] == "venice"]
    assert len(venice_calls) == 3


@pytest.mark.asyncio
async def test_accepts_any_key_does_not_cache_probe_errors():
    validate = AsyncMock(side_effect=[RuntimeError("offline"), {"is_valid": False}])
    with patch("janus.inventory.provider_detection.validate_key", new=validate):
        assert await accepts_any_key("groq") is False
        assert await accepts_any_key("groq") is False
        assert await accepts_any_key("groq") is False

    assert validate.await_count == 2
