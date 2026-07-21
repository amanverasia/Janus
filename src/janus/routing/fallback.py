from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from janus.providers.registry import ProviderRegistry, ResolvedTarget
from janus.routing.capabilities import reorder_combo_by_capabilities
from janus.routing.errors import RETRY_AFTER_CAP_S, get_cooldown
from janus.storage.cooldowns import delete_cooldown, get_active_cooldowns, save_cooldown
from janus.storage.quotas import get_window_usage, window_id
from janus.storage.usage import get_request_counts_today

RPM_WINDOW_SECONDS = 60.0
DEFAULT_COOLDOWN_RETRY_AFTER_S = 60.0
MIN_RETRY_AFTER_S = 1.0

logger = logging.getLogger(__name__)


def _cooldown_task_callback(
    operation: str, account_id: str, model: str
) -> Callable[[asyncio.Task[Any]], None]:
    def _log_failure(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "Cooldown persistence %s failed for account=%s model=%s: %s",
                operation,
                account_id,
                model,
                error,
                exc_info=error,
            )

    return _log_failure


class AccountStrategy(StrEnum):
    FILL_FIRST = "fill_first"
    ROUND_ROBIN = "round_robin"
    STICKY_RR = "sticky_rr"


class AllAccountsCooledDown(Exception):  # noqa: N818
    """Raised when every candidate account for a model/combo is on cooldown.

    Carries `retry_after` (seconds) so callers can surface a 503 with a
    Retry-After header pointing at the earliest cooldown expiry.
    """

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry, db_path: str | Path | None = None) -> None:
        self.registry = registry
        self.db_path = db_path
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._backoff: dict[tuple[str, str], int] = {}
        self._rotation_counters: dict[str, int] = {}
        self._sticky: dict[str, tuple[str, int]] = {}
        self._combo_rotation: dict[str, int] = {}
        self._combo_sticky: dict[str, int] = {}
        self._request_times: dict[str, deque[float]] = {}
        self._daily_counts: dict[str, int] = {}
        self._daily_date: str = self._today()
        self._quota_used: dict[str, int] = {}
        self._quota_window_id: dict[str, str] = {}

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_counts = {}

    @staticmethod
    def _prune_window(times: deque[float], now: float) -> None:
        while times and now - times[0] >= RPM_WINDOW_SECONDS:
            times.popleft()

    def record_request(self, account_id: str) -> None:
        self._roll_day()
        now = time.time()
        times = self._request_times.setdefault(account_id, deque())
        times.append(now)
        self._prune_window(times, now)
        self._daily_counts[account_id] = self._daily_counts.get(account_id, 0) + 1

    def record_attempt(self, target: ResolvedTarget) -> None:
        """Record an upstream attempt: rate-limit counters plus request-metric quota."""
        self.record_request(target.account_id)
        config = target.provider_config
        if config.quota_window and config.quota_limit and config.quota_metric == "requests":
            self._bump_quota(config.row_id, config.quota_window, 1)

    def record_quota_tokens(self, target: ResolvedTarget, tokens: int) -> None:
        """Record consumed tokens for providers with a token-metric quota."""
        config = target.provider_config
        if (
            config.quota_window
            and config.quota_limit
            and config.quota_metric == "tokens"
            and tokens > 0
        ):
            self._bump_quota(config.row_id, config.quota_window, tokens)

    def _bump_quota(self, row_id: str, window: str, amount: int) -> None:
        wid = window_id(window)
        if self._quota_window_id.get(row_id) != wid:
            self._quota_window_id[row_id] = wid
            self._quota_used[row_id] = 0
        self._quota_used[row_id] = self._quota_used.get(row_id, 0) + amount

    def has_quota_headroom(self, target: ResolvedTarget) -> bool:
        config = target.provider_config
        if not config.quota_window or not config.quota_limit:
            return True
        row_id = config.row_id
        if self._quota_window_id.get(row_id) != window_id(config.quota_window):
            return True
        return self._quota_used.get(row_id, 0) < config.quota_limit

    def quota_used(self, row_id: str, window: str) -> int:
        if self._quota_window_id.get(row_id) != window_id(window):
            return 0
        return self._quota_used.get(row_id, 0)

    async def load_quota_usage(self) -> None:
        if self.db_path is None:
            return
        seen: set[str] = set()
        for configs in self.registry.providers.values():
            for config in configs:
                if not config.quota_window or not config.quota_limit:
                    continue
                row_id = config.row_id
                if row_id in seen:
                    continue
                seen.add(row_id)
                usage = await get_window_usage(self.db_path, row_id, config.quota_window)
                metric = "tokens" if config.quota_metric == "tokens" else "requests"
                self._quota_window_id[row_id] = window_id(config.quota_window)
                self._quota_used[row_id] = usage[metric]

    def has_rate_headroom(self, target: ResolvedTarget) -> bool:
        rpm_limit = target.provider_config.rate_limit_rpm
        if rpm_limit is not None and rpm_limit > 0:
            times = self._request_times.get(target.account_id)
            if times is not None:
                self._prune_window(times, time.time())
                if len(times) >= rpm_limit:
                    return False
        rpd_limit = target.provider_config.rate_limit_rpd
        if rpd_limit is not None and rpd_limit > 0:
            self._roll_day()
            if self._daily_counts.get(target.account_id, 0) >= rpd_limit:
                return False
        return True

    def _deprioritize_rate_limited(self, accounts: list[ResolvedTarget]) -> list[ResolvedTarget]:
        if len(accounts) <= 1:
            return accounts
        headroom: list[ResolvedTarget] = []
        limited: list[ResolvedTarget] = []
        for target in accounts:
            if self.has_rate_headroom(target) and self.has_quota_headroom(target):
                headroom.append(target)
            else:
                limited.append(target)
        return headroom + limited

    async def load_request_counts(self) -> None:
        if self.db_path is None:
            return
        self._daily_date = self._today()
        self._daily_counts = await get_request_counts_today(self.db_path)

    def adopt_runtime_state(self, other: FallbackHandler) -> None:
        """Preserve in-memory rotation/rate state across provider reloads."""
        self._rotation_counters = dict(other._rotation_counters)
        self._sticky = dict(other._sticky)
        self._combo_rotation = dict(other._combo_rotation)
        self._combo_sticky = dict(other._combo_sticky)
        self._request_times = {
            account_id: deque(times) for account_id, times in other._request_times.items()
        }
        self._daily_counts = dict(other._daily_counts)
        self._daily_date = other._daily_date
        self._quota_used = dict(other._quota_used)
        self._quota_window_id = dict(other._quota_window_id)
        # Cooldowns/backoff are also reloaded from DB; keep live values as a baseline.
        self._cooldowns = dict(other._cooldowns)
        self._backoff = dict(other._backoff)

    @staticmethod
    def _client_pool_key(
        pool_key: str,
        *,
        client_key_id: int | None,
        sticky_client_key: bool,
    ) -> str:
        if sticky_client_key and client_key_id is not None:
            return f"{pool_key}::ck{client_key_id}"
        return pool_key

    def _phase_accounts(
        self,
        accounts: list[ResolvedTarget],
        *,
        client_key_id: int | None,
        sticky_client_key: bool,
    ) -> list[ResolvedTarget]:
        if not (sticky_client_key and client_key_id is not None) or len(accounts) <= 1:
            return accounts
        index = client_key_id % len(accounts)
        return accounts[index:] + accounts[:index]

    def _rotate_accounts(
        self,
        pool_key: str,
        accounts: list[ResolvedTarget],
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
    ) -> list[ResolvedTarget]:
        if len(accounts) <= 1:
            return accounts
        phased = self._phase_accounts(
            accounts,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )
        counter_key = self._client_pool_key(
            pool_key,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )
        index = self._rotation_counters.get(counter_key, 0) % len(phased)
        self._rotation_counters[counter_key] = index + 1
        return phased[index:] + phased[:index]

    def _sticky_rotate(
        self,
        pool_key: str,
        accounts: list[ResolvedTarget],
        sticky_limit: int,
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
    ) -> list[ResolvedTarget]:
        phased = self._phase_accounts(
            accounts,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )
        counter_key = self._client_pool_key(
            pool_key,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )
        account_ids = [a.account_id for a in phased]
        sticky = self._sticky.get(counter_key)
        if sticky is not None:
            head_id, count = sticky
            if head_id in account_ids and count < sticky_limit:
                self._sticky[counter_key] = (head_id, count + 1)
                index = account_ids.index(head_id)
                return phased[index:] + phased[:index]
        index = self._rotation_counters.get(counter_key, 0) % len(phased)
        self._rotation_counters[counter_key] = index + 1
        rotated = phased[index:] + phased[:index]
        self._sticky[counter_key] = (rotated[0].account_id, 1)
        return rotated

    def _order_by_strategy(
        self,
        pool_key: str,
        accounts: list[ResolvedTarget],
        strategy: AccountStrategy,
        sticky_limit: int,
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
    ) -> list[ResolvedTarget]:
        if len(accounts) <= 1:
            return accounts
        if strategy is AccountStrategy.FILL_FIRST:
            if sticky_client_key and client_key_id is not None:
                return self._phase_accounts(
                    accounts,
                    client_key_id=client_key_id,
                    sticky_client_key=True,
                )
            return accounts
        if strategy is AccountStrategy.STICKY_RR:
            return self._sticky_rotate(
                pool_key,
                accounts,
                sticky_limit,
                client_key_id=client_key_id,
                sticky_client_key=sticky_client_key,
            )
        return self._rotate_accounts(
            pool_key,
            accounts,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )

    def _resolve_order(
        self,
        pool_key: str,
        accounts: list[ResolvedTarget],
        *,
        client_key_id: int | None,
        sticky_client_key: bool,
        strategy: AccountStrategy,
        sticky_limit: int,
    ) -> list[ResolvedTarget]:
        return self._order_by_strategy(
            pool_key,
            accounts,
            strategy,
            sticky_limit,
            client_key_id=client_key_id,
            sticky_client_key=sticky_client_key,
        )

    def resolve_attempts(
        self,
        model_str: str,
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
        strategy: AccountStrategy = AccountStrategy.ROUND_ROBIN,
        sticky_limit: int = 3,
        required_caps: frozenset[str] = frozenset(),
        combo_strategy: str = "fallback",
        combo_sticky_limit: int = 1,
    ) -> list[ResolvedTarget]:
        # Synchronous by design: the rotation-counter and sticky read-modify-writes
        # below have no await between read and write, so they are an atomic critical
        # section under the single-threaded event loop (no lock needed).
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            if combo_strategy == "round_robin" and len(combo_models) > 1:
                idx = self._combo_rotation.get(model_str, 0) % len(combo_models)
                if combo_sticky_limit > 1:
                    used = self._combo_sticky.get(model_str, 0) + 1
                    if used >= combo_sticky_limit:
                        self._combo_rotation[model_str] = idx + 1
                        self._combo_sticky[model_str] = 0
                    else:
                        self._combo_sticky[model_str] = used
                else:
                    self._combo_rotation[model_str] = idx + 1
                combo_models = combo_models[idx:] + combo_models[:idx]
            if required_caps:
                combo_models = reorder_combo_by_capabilities(combo_models, required_caps)
            all_attempts: list[ResolvedTarget] = []
            all_candidate_ids: list[str] = []
            earliest_expiry: float | None = None
            for m in combo_models:
                _, _, specific = m.partition("/")
                targets = self.registry.lookup(m)
                if targets:
                    all_candidate_ids.extend(t.account_id for t in targets)
                    m_expiry = self.earliest_cooldown_expiry(
                        [t.account_id for t in targets], specific
                    )
                    if m_expiry is not None and (
                        earliest_expiry is None or m_expiry < earliest_expiry
                    ):
                        earliest_expiry = m_expiry
                    available = [t for t in targets if self.is_available(t.account_id, specific)]
                    all_attempts.extend(
                        self._deprioritize_rate_limited(
                            self._resolve_order(
                                m,
                                available,
                                client_key_id=client_key_id,
                                sticky_client_key=sticky_client_key,
                                strategy=strategy,
                                sticky_limit=sticky_limit,
                            )
                        )
                    )
            if not all_attempts:
                if all_candidate_ids and earliest_expiry is not None:
                    retry_after = max(earliest_expiry - time.time(), MIN_RETRY_AFTER_S)
                    raise AllAccountsCooledDown(
                        f"No available providers for combo '{model_str}' "
                        "(all accounts cooling down)",
                        retry_after=retry_after,
                    )
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        _, _, specific_model = model_str.partition("/")
        available = [t for t in targets if self.is_available(t.account_id, specific_model)]
        if not available:
            expiry = self.earliest_cooldown_expiry([t.account_id for t in targets], specific_model)
            retry_after = (
                max(expiry - time.time(), MIN_RETRY_AFTER_S)
                if expiry is not None
                else DEFAULT_COOLDOWN_RETRY_AFTER_S
            )
            raise AllAccountsCooledDown(
                f"No available providers for '{model_str}' (all accounts cooled down)",
                retry_after=retry_after,
            )
        return self._deprioritize_rate_limited(
            self._resolve_order(
                model_str,
                available,
                client_key_id=client_key_id,
                sticky_client_key=sticky_client_key,
                strategy=strategy,
                sticky_limit=sticky_limit,
            )
        )

    def mark_cooldown(
        self,
        account_id: str,
        error_type: str,
        model: str | None = None,
        retry_after: float | None = None,
        duration: float | None = None,
    ) -> None:
        model_key = model or "__all__"
        key = (account_id, model_key)
        if duration is not None:
            # Explicit override: use the given duration and freeze the backoff level.
            cooldown, level = duration, self._backoff.get(key, 0)
        elif retry_after is not None:
            cooldown, level = min(retry_after, RETRY_AFTER_CAP_S), 0
        else:
            cooldown, level = get_cooldown(error_type, self._backoff.get(key, 0))
        self._backoff[key] = level
        expires_at = time.time() + cooldown
        self._cooldowns[key] = expires_at
        if self.db_path is not None:
            self._persist_cooldown(account_id, model_key, expires_at, error_type, level)

    def mark_success(self, account_id: str, model: str | None = None) -> None:
        # A model-scoped success clears only that (account, model) cooldown.
        # An account-level success (model=None) clears the account-wide __all__ lock.
        keys = {"__all__"} if model is None else {model}
        for mk in keys:
            self._cooldowns.pop((account_id, mk), None)
            self._backoff.pop((account_id, mk), None)
            if self.db_path is not None:
                self._delete_cooldown(account_id, mk)

    def _persist_cooldown(
        self, account_id: str, model: str, expires_at: float, error_type: str, level: int
    ) -> None:
        assert self.db_path is not None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            save_cooldown(
                self.db_path,
                account_id,
                expires_at,
                model=model,
                error_type=error_type,
                backoff_level=level,
            )
        )
        task.add_done_callback(_cooldown_task_callback("save", account_id, model))

    def _delete_cooldown(self, account_id: str, model: str) -> None:
        assert self.db_path is not None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(delete_cooldown(self.db_path, account_id, model))
        task.add_done_callback(_cooldown_task_callback("delete", account_id, model))

    async def load_cooldowns(self) -> None:
        if self.db_path is None:
            return
        active = await get_active_cooldowns(self.db_path)
        for combined, (expires_at, level) in active.items():
            account_id, _, model = combined.partition("::")
            self._cooldowns[(account_id, model)] = expires_at
            if level:
                self._backoff[(account_id, model)] = level

    def earliest_cooldown_expiry(
        self, account_ids: list[str], model: str | None = None
    ) -> float | None:
        """Earliest cooldown expiry (unix epoch seconds) across the given accounts.

        Considers both the account-wide `__all__` lock and a model-specific
        lock (when `model` is given) for each account.
        """
        now = time.time()
        expiries: list[float] = []
        for account_id in account_ids:
            all_exp = self._cooldowns.get((account_id, "__all__"))
            if all_exp is not None and all_exp > now:
                expiries.append(all_exp)
            if model is not None:
                model_exp = self._cooldowns.get((account_id, model))
                if model_exp is not None and model_exp > now:
                    expiries.append(model_exp)
        if not expiries:
            return None
        return min(expiries)

    def is_available(self, account_id: str, model: str | None = None) -> bool:
        now = time.time()
        all_exp = self._cooldowns.get((account_id, "__all__"))
        if all_exp is not None and now < all_exp:
            return False
        if model is not None:
            exp = self._cooldowns.get((account_id, model))
            if exp is not None and now < exp:
                return False
        return True
