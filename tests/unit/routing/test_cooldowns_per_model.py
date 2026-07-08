from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler


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
