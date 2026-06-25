from __future__ import annotations

import time

from janus.providers.registry import ProviderRegistry, ResolvedTarget

COOLDOWN_DURATIONS: dict[str, float] = {
    "rate_limit": 60.0,
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self._cooldowns: dict[str, float] = {}

    def resolve_attempts(self, model_str: str) -> list[ResolvedTarget]:
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            all_attempts: list[ResolvedTarget] = []
            for m in combo_models:
                targets = self.registry.lookup(m)
                if targets:
                    all_attempts.extend(t for t in targets if self.is_available(t.account_id))
            if not all_attempts:
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        available = [t for t in targets if self.is_available(t.account_id)]
        if not available:
            raise ValueError(f"No available providers for '{model_str}' (all accounts cooled down)")
        return available

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
        self._cooldowns[account_id] = time.monotonic() + cooldown

    def is_available(self, account_id: str) -> bool:
        expiry = self._cooldowns.get(account_id)
        if expiry is None:
            return True
        return time.monotonic() >= expiry
