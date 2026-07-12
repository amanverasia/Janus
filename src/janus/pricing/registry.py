from __future__ import annotations

from .builtin import BUILTIN_PRICING
from .models import ModelPricing

# Layer resolution order: override beats catalog beats builtin. Each layer is
# checked in full (exact match, then longest-prefix match) before falling
# through to the next layer. This means an override that only *prefix*
# matches a model still wins over an exact match in catalog/builtin — an
# override always wins when it matches at all, even loosely.
_LAYER_ORDER = ("override", "catalog", "builtin")


class PricingRegistry:
    """Resolves model pricing across three precedence layers.

    Layers, from highest to lowest precedence: ``override`` (user-configured
    pricing overrides), ``catalog`` (synced from LiteLLM/OpenRouter), and
    ``builtin`` (hardcoded defaults shipped with Janus).

    Resolution is layered, not flattened: for a given model name, each layer
    is checked in precedence order using the same exact-then-prefix matching
    logic, and the first layer that produces *any* match wins outright —
    even if a lower-priority layer would have produced a more specific
    (longer-prefix or exact) match. In other words, an override prefix-match
    beats a more specific lower-layer entry: overrides always win when they
    match, regardless of specificity. For example, if overrides contains
    ``"gpt-4o"`` and catalog contains the more specific ``"gpt-4o-mini"``,
    looking up ``"gpt-4o-mini-2024-07-18"`` resolves via the override's
    prefix match on ``"gpt-4o"``, not the catalog's closer match.
    """

    def __init__(
        self,
        user_overrides: dict[str, dict[str, float]],
        catalog: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._layers: dict[str, dict[str, ModelPricing]] = {
            "override": {model: ModelPricing(**rates) for model, rates in user_overrides.items()},
            "catalog": {model: ModelPricing(**rates) for model, rates in (catalog or {}).items()},
            "builtin": dict(BUILTIN_PRICING),
        }

    def source_of(self, model: str) -> str | None:
        for layer_name in _LAYER_ORDER:
            if self._match(self._layers[layer_name], model) is not None:
                return layer_name
        return None

    def get(self, model: str) -> ModelPricing | None:
        for layer_name in _LAYER_ORDER:
            table = self._layers[layer_name]
            match = self._match(table, model)
            if match is not None:
                return match
        return None

    def _match(self, table: dict[str, ModelPricing], model: str) -> ModelPricing | None:
        candidates = [model]
        if "/" in model:
            candidates.append(model.rsplit("/", 1)[1])
        for candidate in candidates:
            match = self._match_one(table, candidate)
            if match is not None:
                return match
        return None

    def _match_one(self, table: dict[str, ModelPricing], model: str) -> ModelPricing | None:
        if model in table:
            return table[model]
        parts = model.split("-")
        for i in range(len(parts) - 1, 0, -1):
            prefix = "-".join(parts[:i])
            if prefix in table:
                return table[prefix]
        return None

    def get_all(self) -> dict[str, ModelPricing]:
        merged: dict[str, ModelPricing] = {}
        for layer_name in reversed(_LAYER_ORDER):
            merged.update(self._layers[layer_name])
        return merged
