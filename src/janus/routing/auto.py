from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from janus.canonical.models import CanonicalRequest
from janus.pricing.registry import PricingRegistry
from janus.providers.registry import ProviderRegistry
from janus.routing.capabilities import detect_required_capabilities, get_capabilities_for_model
from janus.storage.key_access import model_allowed
from janus.storage.model_signals import ModelSignals, SignalStats, get_model_signals

logger = logging.getLogger(__name__)

AUTO_MODEL = "auto"
TOP_N = 5
_VERSION_SUFFIX = re.compile(r"-(\d{3,})$")


class AutoStrategy(StrEnum):
    BALANCED = "balanced"
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    QUALITY = "quality"


VALID_AUTO_STRATEGIES = frozenset(strategy.value for strategy in AutoStrategy)


@dataclass
class ScoredCandidate:
    model_str: str
    base: str
    tier: int
    score: float
    blended_cost: float | None
    error_rate: float | None
    tps_p50: float | None
    ttft_p50_ms: int | None
    samples: int
    overridden: bool
    quality: float | None


@dataclass(frozen=True)
class AutoPlan:
    strategy: str
    requested: str
    models: list[str] = field(default_factory=list)
    trace: list[ScoredCandidate] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)


def canonical_model_base(model: str) -> str:
    if _VERSION_SUFFIX.search(model):
        return _VERSION_SUFFIX.sub("", model)
    return model


def _bare(model_str: str) -> str:
    return model_str.split("/", 1)[1] if "/" in model_str else model_str


def _blended_cost(pricing: Any) -> float:
    return 0.3 * float(pricing.input_per_mtok) + 0.7 * float(pricing.output_per_mtok)


def _minmax(values: list[float]) -> dict[float, float]:
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi - lo <= 0:
        return {value: 1.0 for value in values}
    return {value: (value - lo) / (hi - lo) for value in values}


def _quality_axis(stats: SignalStats | None, override: float | None) -> float | None:
    if override is not None:
        return float(override)
    if stats is None or not stats.sufficient:
        return None
    error_rate = stats.error_rate if stats.error_rate is not None else 0.0
    return 1.0 - float(error_rate)


def collect_candidates(registry: ProviderRegistry) -> list[str]:
    return sorted(registry.auto_candidates())


def score_candidates(
    candidates: list[str],
    *,
    strategy: AutoStrategy,
    pricing_registry: PricingRegistry,
    signals: ModelSignals | None,
    overrides: dict[str, float] | None = None,
) -> list[ScoredCandidate]:
    override_map = overrides or {}
    scored: list[ScoredCandidate] = []
    for model_str in candidates:
        bare = _bare(model_str)
        pricing = pricing_registry.get(bare)
        stats = signals.by_model.get(bare) if signals is not None else None
        override = override_map.get(bare)
        if override is None:
            override = override_map.get(model_str)
        cost = _blended_cost(pricing) if pricing is not None else None
        quality = _quality_axis(stats, override)
        tps_value = stats.tps_p50 if stats is not None else None
        ttft_value = stats.ttft_p50_ms if stats is not None else None
        has_tps = bool(stats and stats.sufficient and tps_value is not None)
        has_ttft = bool(stats and stats.sufficient and ttft_value is not None)
        if strategy == AutoStrategy.CHEAPEST:
            tier = 0 if cost is not None else 2
        elif strategy == AutoStrategy.QUALITY:
            tier = 0 if quality is not None else 2
        else:
            tier = 0 if (has_tps and has_ttft and cost is not None) else 2
        scored.append(
            ScoredCandidate(
                model_str=model_str,
                base=canonical_model_base(bare),
                tier=tier,
                score=0.0,
                blended_cost=cost,
                error_rate=stats.error_rate if stats is not None else None,
                tps_p50=tps_value if has_tps else None,
                ttft_p50_ms=ttft_value if has_ttft else None,
                samples=stats.samples if stats is not None else 0,
                overridden=override is not None,
                quality=quality,
            )
        )

    top = [candidate for candidate in scored if candidate.tier == 0]
    if top and strategy != AutoStrategy.CHEAPEST:
        costs = [candidate.blended_cost or 0.0 for candidate in top]
        cost_norm = _minmax(costs)
        if strategy == AutoStrategy.QUALITY:
            qualities = [candidate.quality or 0.0 for candidate in top]
            quality_norm = _minmax(qualities)
            for candidate in top:
                candidate.score = quality_norm[candidate.quality or 0.0]
        else:
            tps_values = [candidate.tps_p50 or 0.0 for candidate in top]
            ttft_values = [float(candidate.ttft_p50_ms or 0) for candidate in top]
            tps_norm = _minmax(tps_values)
            ttft_norm = _minmax(ttft_values)
            for candidate in top:
                speed = 0.5 * tps_norm[candidate.tps_p50 or 0.0] + 0.5 * (
                    1.0 - ttft_norm[float(candidate.ttft_p50_ms or 0)]
                )
                value = 1.0 - cost_norm[candidate.blended_cost or 0.0]
                if strategy == AutoStrategy.FASTEST:
                    candidate.score = speed
                else:
                    quality = candidate.quality
                    candidate.score = 0.5 * quality + 0.5 * value if quality is not None else speed
    elif top and strategy == AutoStrategy.CHEAPEST:
        for candidate in top:
            candidate.score = -float(candidate.blended_cost or 0.0)

    scored.sort(key=lambda candidate: (candidate.tier, -candidate.score, candidate.model_str))
    return scored


def serialize_trace(plan: AutoPlan, *, limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "model": candidate.model_str,
            "tier": candidate.tier,
            "score": round(candidate.score, 4),
            "blended_cost": candidate.blended_cost,
            "error_rate": candidate.error_rate,
            "tps_p50": candidate.tps_p50,
            "ttft_p50_ms": candidate.ttft_p50_ms,
            "samples": candidate.samples,
            "overridden": candidate.overridden,
        }
        for candidate in plan.trace[: max(1, limit)]
    ]


def _caps_satisfied(model_str: str, required: frozenset[str]) -> bool:
    if not required:
        return True
    prefix = model_str.split("/", 1)[0] if "/" in model_str else model_str
    bare = _bare(model_str)
    caps = get_capabilities_for_model(prefix, bare)
    return all(bool(caps.get(cap)) for cap in required)


async def plan_auto(
    *,
    registry: ProviderRegistry,
    db_path: str | Path,
    pricing_registry: PricingRegistry,
    request: CanonicalRequest | None = None,
    strategy: AutoStrategy = AutoStrategy.BALANCED,
    allowed_models: list[str] | None = None,
    overrides: dict[str, float] | None = None,
    top_n: int = TOP_N,
) -> AutoPlan:
    required = detect_required_capabilities(request) if request is not None else frozenset()
    candidates = collect_candidates(registry)
    excluded: list[tuple[str, str]] = []
    if allowed_models:
        candidates = [model for model in candidates if model_allowed(model, allowed_models)]
    if request is not None:
        for model in candidates:
            if not _caps_satisfied(model, required):
                excluded.append((model, "capabilities"))
        candidates = [model for model in candidates if _caps_satisfied(model, required)]
    unpriced = [model for model in candidates if pricing_registry.get(_bare(model)) is None]
    for model in unpriced:
        excluded.append((model, "unpriced"))
    candidates = [model for model in candidates if model not in set(unpriced)]

    signals: ModelSignals | None = None
    try:
        signals = await get_model_signals(db_path)
    except Exception:
        logger.warning("Model signals unavailable for auto routing", exc_info=True)

    scored = score_candidates(
        candidates,
        strategy=strategy,
        pricing_registry=pricing_registry,
        signals=signals,
        overrides=overrides,
    )

    deduped: list[ScoredCandidate] = []
    seen_bases: set[str] = set()
    for candidate in scored:
        if candidate.base in seen_bases:
            excluded.append((candidate.model_str, "version sibling"))
            continue
        seen_bases.add(candidate.base)
        deduped.append(candidate)

    chain = [candidate.model_str for candidate in deduped[: max(1, top_n)]]
    return AutoPlan(
        strategy=strategy.value,
        requested=AUTO_MODEL,
        models=chain,
        trace=deduped,
        excluded=excluded,
    )
