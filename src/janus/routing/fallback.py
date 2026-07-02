from __future__ import annotations

import asyncio
import time
from pathlib import Path

from janus.providers.registry import ProviderRegistry, ResolvedTarget
from janus.storage.cooldowns import get_active_cooldowns, save_cooldown

COOLDOWN_DURATIONS: dict[str, float] = {
    "rate_limit": 60.0,
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry, db_path: str | Path | None = None) -> None:
        self.registry = registry
        self.db_path = db_path
        self._cooldowns: dict[str, float] = {}
        self._rotation_counters: dict[str, int] = {}

    def _rotate_accounts(
        self, pool_key: str, accounts: list[ResolvedTarget]
    ) -> list[ResolvedTarget]:
        if len(accounts) <= 1:
            return accounts
        index = self._rotation_counters.get(pool_key, 0) % len(accounts)
        self._rotation_counters[pool_key] = index + 1
        return accounts[index:] + accounts[:index]

    def resolve_attempts(self, model_str: str) -> list[ResolvedTarget]:
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            all_attempts: list[ResolvedTarget] = []
            for m in combo_models:
                targets = self.registry.lookup(m)
                if targets:
                    available = [t for t in targets if self.is_available(t.account_id)]
                    all_attempts.extend(self._rotate_accounts(m, available))
            if not all_attempts:
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        available = [t for t in targets if self.is_available(t.account_id)]
        if not available:
            raise ValueError(f"No available providers for '{model_str}' (all accounts cooled down)")
        return self._rotate_accounts(model_str, available)

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
