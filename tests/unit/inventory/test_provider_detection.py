from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from janus.inventory.provider_detection import (
    detectable_provider_ids,
    find_authenticating_provider,
    resolve_provider_for_key,
)


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
        del key_value, metadata, skip_probe
        return {"is_valid": provider_id == "groq"}

    with patch(
        "janus.inventory.provider_detection.validate_key",
        new=AsyncMock(side_effect=fake_validate),
    ):
        result = await find_authenticating_provider("secret", ["openai", "groq", "anthropic"])

    assert result == "groq"


@pytest.mark.asyncio
async def test_find_authenticating_provider_prefers_lower_rank():
    async def fake_validate(key_value: str, provider_id: str, metadata, *, skip_probe: bool):
        del key_value, metadata, skip_probe
        return {"is_valid": provider_id in {"openai", "groq"}}

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
