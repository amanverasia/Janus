from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from janus.providers.registry import ProviderRegistry, ResolvedTarget
from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.usage import get_request_counts_today

COOLDOWN_DURATIONS: dict[str, float] = {
    "rate_limit": 60.0,
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}

RPM_WINDOW_SECONDS = 60.0


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry, db_path: str | Path | None = None) -> None:
        self.registry = registry
        self.db_path = db_path
        self._cooldowns: dict[str, float] = {}
        self._rotation_counters: dict[str, int] = {}
        self._request_times: dict[str, deque[float]] = {}
        self._daily_counts: dict[str, int] = {}
        self._daily_date: str = self._today()

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
            (headroom if self.has_rate_headroom(target) else limited).append(target)
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

    def resolve_attempts(
        self,
        model_str: str,
        *,
        client_key_id: int | None = None,
        sticky_client_key: bool = False,
    ) -> list[ResolvedTarget]:
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            all_attempts: list[ResolvedTarget] = []
            for m in combo_models:
                targets = self.registry.lookup(m)
                if targets:
                    available = [t for t in targets if self.is_available(t.account_id)]
                    all_attempts.extend(
                        self._deprioritize_rate_limited(
                            self._rotate_accounts(
                                m,
                                available,
                                client_key_id=client_key_id,
                                sticky_client_key=sticky_client_key,
                            )
                        )
                    )
            if not all_attempts:
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        available = [t for t in targets if self.is_available(t.account_id)]
        if not available:
            raise ValueError(f"No available providers for '{model_str}' (all accounts cooled down)")
        return self._deprioritize_rate_limited(
            self._rotate_accounts(
                model_str,
                available,
                client_key_id=client_key_id,
                sticky_client_key=sticky_client_key,
            )
        )

    def mark_cooldown(
        self,
        account_id: str,
        error_type: str,
        retry_after: float | None = None,
        duration: float | None = None,
    ) -> None:
        if duration is not None:
            cooldown = duration
        elif retry_after is not None:
            cooldown = retry_after
        else:
            cooldown = COOLDOWN_DURATIONS.get(error_type, 60.0)
        self._cooldowns[account_id] = time.time() + cooldown
        if self.db_path is not None:
            self._persist_cooldown(account_id, self._cooldowns[account_id])

    def _persist_cooldown(self, account_id: str, expires_at: float) -> None:
        assert self.db_path is not None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(save_cooldown(self.db_path, account_id, expires_at))

    async def load_cooldowns(self) -> None:
        if self.db_path is None:
            return
        self._cooldowns = await get_active_cooldowns(self.db_path)

    def is_available(self, account_id: str) -> bool:
        expiry = self._cooldowns.get(account_id)
        if expiry is None:
            return True
        return time.time() >= expiry
