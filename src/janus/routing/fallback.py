from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from janus.providers.registry import ProviderRegistry, ResolvedTarget
from janus.routing.capabilities import reorder_combo_by_capabilities
from janus.routing.errors import RETRY_AFTER_CAP_S, get_cooldown
from janus.storage.cooldowns import delete_cooldown, get_active_cooldowns, save_cooldown
from janus.storage.quotas import get_window_usage, window_id
from janus.storage.usage import get_request_counts_today

RPM_WINDOW_SECONDS = 60.0


class AccountStrategy(StrEnum):
    FILL_FIRST = "fill_first"
    ROUND_ROBIN = "round_robin"
    STICKY_RR = "sticky_rr"


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry, db_path: str | Path | None = None) -> None:
        self.registry = registry
        self.db_path = db_path
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._backoff: dict[tuple[str, str], int] = {}
        self._rotation_counters: dict[str, int] = {}
        self._sticky: dict[str, tuple[str, int]] = {}
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
        if sticky_client_key and client_key_id is not None:
            index = client_key_id % len(accounts)
            return accounts[index:] + accounts[:index]
        index = self._rotation_counters.get(pool_key, 0) % len(accounts)
        self._rotation_counters[pool_key] = index + 1
        return accounts[index:] + accounts[:index]

    def _sticky_rotate(
        self, pool_key: str, accounts: list[ResolvedTarget], sticky_limit: int
    ) -> list[ResolvedTarget]:
        account_ids = [a.account_id for a in accounts]
        sticky = self._sticky.get(pool_key)
        if sticky is not None:
            head_id, count = sticky
            if head_id in account_ids and count < sticky_limit:
                self._sticky[pool_key] = (head_id, count + 1)
                index = account_ids.index(head_id)
                return accounts[index:] + accounts[:index]
        rotated = self._rotate_accounts(pool_key, accounts)
        self._sticky[pool_key] = (rotated[0].account_id, 1)
        return rotated

    def _order_by_strategy(
        self,
        pool_key: str,
        accounts: list[ResolvedTarget],
        strategy: AccountStrategy,
        sticky_limit: int,
    ) -> list[ResolvedTarget]:
        if len(accounts) <= 1:
            return accounts
        if strategy is AccountStrategy.FILL_FIRST:
            return accounts
        if strategy is AccountStrategy.STICKY_RR:
            return self._sticky_rotate(pool_key, accounts, sticky_limit)
        return self._rotate_accounts(pool_key, accounts)

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
        if sticky_client_key and client_key_id is not None:
            return self._rotate_accounts(
                pool_key,
                accounts,
                client_key_id=client_key_id,
                sticky_client_key=sticky_client_key,
            )
        return self._order_by_strategy(pool_key, accounts, strategy, sticky_limit)

    def resolve_attempts(
        self,
        model_str: str,
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
        strategy: AccountStrategy = AccountStrategy.ROUND_ROBIN,
        sticky_limit: int = 3,
        required_caps: frozenset[str] = frozenset(),
    ) -> list[ResolvedTarget]:
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            if required_caps:
                combo_models = reorder_combo_by_capabilities(combo_models, required_caps)
            all_attempts: list[ResolvedTarget] = []
            for m in combo_models:
                _, _, specific = m.partition("/")
                targets = self.registry.lookup(m)
                if targets:
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
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        _, _, specific_model = model_str.partition("/")
        available = [t for t in targets if self.is_available(t.account_id, specific_model)]
        if not available:
            raise ValueError(f"No available providers for '{model_str}' (all accounts cooled down)")
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
        for mk in {model or "__all__", "__all__"}:
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
        loop.create_task(
            save_cooldown(
                self.db_path,
                account_id,
                expires_at,
                model=model,
                error_type=error_type,
                backoff_level=level,
            )
        )

    def _delete_cooldown(self, account_id: str, model: str) -> None:
        assert self.db_path is not None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(delete_cooldown(self.db_path, account_id, model))

    async def load_cooldowns(self) -> None:
        if self.db_path is None:
            return
        active = await get_active_cooldowns(self.db_path)
        for combined, (expires_at, level) in active.items():
            account_id, _, model = combined.partition("::")
            self._cooldowns[(account_id, model)] = expires_at
            if level:
                self._backoff[(account_id, model)] = level

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
