import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest

from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import (
    AllAccountsCooledDown,
    FallbackHandler,
    _cooldown_task_callback,
)


def _handler() -> FallbackHandler:
    return FallbackHandler(ProviderRegistry(), db_path=None)


def test_model_cooldown_does_not_block_other_model() -> None:
    h = _handler()
    h.mark_cooldown("acct-a", "rate_limit", model="gpt-4o")
    assert not h.is_available("acct-a", "gpt-4o")
    assert h.is_available("acct-a", "gpt-4o-mini")


def test_all_cooldown_blocks_every_model() -> None:
    h = _handler()
    h.mark_cooldown("acct-a", "auth_error")
    assert not h.is_available("acct-a", "gpt-4o")
    assert not h.is_available("acct-a", "anything")
    assert not h.is_available("acct-a")


def test_backoff_escalates_then_success_resets() -> None:
    h = _handler()
    h.mark_cooldown("acct-b", "rate_limit", model="m")
    first = h._cooldowns[("acct-b", "m")]
    assert first is not None
    h.mark_success("acct-b", "m")
    assert h.is_available("acct-b", "m")
    assert ("acct-b", "m") not in h._backoff or h._backoff[("acct-b", "m")] == 0


def test_retry_after_overrides_backoff() -> None:
    import time

    h = _handler()
    h.mark_cooldown("acct-c", "rate_limit", model="m", retry_after=120.0)
    remaining = h._cooldowns[("acct-c", "m")] - time.time()
    assert 100 < remaining <= 120


def test_backoff_escalates_on_repeated_rate_limit() -> None:
    h = _handler()
    h.mark_cooldown("acct-d", "rate_limit", model="m")
    first_level = h._backoff[("acct-d", "m")]
    h.mark_cooldown("acct-d", "rate_limit", model="m")
    second_level = h._backoff[("acct-d", "m")]
    assert second_level > first_level


def test_model_success_does_not_clear_account_all_cooldown() -> None:
    # B2 contract: a model-scoped success must NOT clear the account-wide __all__ lock.
    h = _handler()
    h.mark_cooldown("acct-a", "auth_error")  # account-level __all__ cooldown
    h.mark_success("acct-a", "m1")  # a different model succeeded
    assert not h.is_available("acct-a", "m1")  # still blocked by __all__


def test_account_success_clears_all_cooldown() -> None:
    h = _handler()
    h.mark_cooldown("acct-a", "auth_error")
    h.mark_success("acct-a")  # account-level success
    assert h.is_available("acct-a", "anything")


def test_model_success_clears_only_that_model() -> None:
    h = _handler()
    h.mark_cooldown("acct-a", "rate_limit", model="m1")
    h.mark_cooldown("acct-a", "rate_limit", model="m2")
    h.mark_success("acct-a", "m1")
    assert h.is_available("acct-a", "m1")
    assert not h.is_available("acct-a", "m2")


def test_earliest_cooldown_expiry_ignores_stale_entries() -> None:
    h = _handler()
    active_expiry = time.time() + 300
    h._cooldowns[("acct-a", "__all__")] = time.time() - 30
    h._cooldowns[("acct-a", "m1")] = active_expiry

    assert h.earliest_cooldown_expiry(["acct-a"], "m1") == active_expiry


def test_earliest_cooldown_expiry_returns_none_when_all_entries_are_stale() -> None:
    h = _handler()
    h._cooldowns[("acct-a", "__all__")] = time.time() - 30
    h._cooldowns[("acct-a", "m1")] = time.time() - 10

    assert h.earliest_cooldown_expiry(["acct-a"], "m1") is None


def test_stale_account_cooldown_does_not_shrink_retry_after() -> None:
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="acct-a",
            prefix="provider",
            api_type="openai_compat",
            base_url="https://example.test/v1",
            models=["m1"],
        )
    )
    h = FallbackHandler(registry, db_path=None)
    h._cooldowns[("acct-a", "__all__")] = time.time() - 30
    h.mark_cooldown("acct-a", "rate_limit", model="m1", duration=300)

    with pytest.raises(AllAccountsCooledDown) as exc_info:
        h.resolve_attempts("provider/m1")

    assert 290 < exc_info.value.retry_after <= 300


async def test_cooldown_save_failure_is_logged_and_keeps_memory_state(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    h = FallbackHandler(ProviderRegistry(), db_path=tmp_path / "janus.db")
    with (
        caplog.at_level(logging.WARNING, logger="janus.routing.fallback"),
        patch(
            "janus.routing.fallback.save_cooldown",
            AsyncMock(side_effect=OSError("disk full")),
        ),
    ):
        h.mark_cooldown("acct-a", "rate_limit", model="m1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert ("acct-a", "m1") in h._cooldowns
    assert "Cooldown persistence save failed" in caplog.text
    assert "account=acct-a model=m1" in caplog.text
    assert "disk full" in caplog.text


async def test_cooldown_delete_failure_is_logged_and_clears_memory_state(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    h = FallbackHandler(ProviderRegistry(), db_path=tmp_path / "janus.db")
    h._cooldowns[("acct-b", "m2")] = time.time() + 60
    h._backoff[("acct-b", "m2")] = 1
    with (
        caplog.at_level(logging.WARNING, logger="janus.routing.fallback"),
        patch(
            "janus.routing.fallback.delete_cooldown",
            AsyncMock(side_effect=OSError("database locked")),
        ),
    ):
        h.mark_success("acct-b", "m2")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert ("acct-b", "m2") not in h._cooldowns
    assert ("acct-b", "m2") not in h._backoff
    assert "Cooldown persistence delete failed" in caplog.text
    assert "account=acct-b model=m2" in caplog.text
    assert "database locked" in caplog.text


async def test_successful_cooldown_persistence_does_not_warn(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    h = FallbackHandler(ProviderRegistry(), db_path=tmp_path / "janus.db")
    with (
        caplog.at_level(logging.WARNING, logger="janus.routing.fallback"),
        patch("janus.routing.fallback.save_cooldown", AsyncMock()),
    ):
        h.mark_cooldown("acct-c", "rate_limit", model="m3")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "Cooldown persistence" not in caplog.text


async def test_cancelled_cooldown_persistence_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    task.add_done_callback(_cooldown_task_callback("save", "acct-d", "m4"))

    with caplog.at_level(logging.WARNING, logger="janus.routing.fallback"):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert "Cooldown persistence" not in caplog.text
