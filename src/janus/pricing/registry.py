from __future__ import annotations

from .builtin import BUILTIN_PRICING
from .models import ModelPricing


class PricingRegistry:
    def __init__(self, user_overrides: dict[str, dict[str, float]]) -> None:
        self._table: dict[str, ModelPricing] = {**BUILTIN_PRICING}
        for model, rates in user_overrides.items():
            self._table[model] = ModelPricing(**rates)

    def get(self, model: str) -> ModelPricing | None:
        candidates = [model]
        if "/" in model:
            candidates.append(model.rsplit("/", 1)[1])
        for candidate in candidates:
            match = self._match(candidate)
            if match is not None:
                return match
        return None

    def _match(self, model: str) -> ModelPricing | None:
        if model in self._table:
            return self._table[model]
        parts = model.split("-")
        for i in range(len(parts) - 1, 0, -1):
            prefix = "-".join(parts[:i])
            if prefix in self._table:
                return self._table[prefix]
        return None

    def get_all(self) -> dict[str, ModelPricing]:
        return dict(self._table)
