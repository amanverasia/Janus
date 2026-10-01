from __future__ import annotations

import datetime

import pytest

from janus.canonical.models import CanonicalRequest
from janus.config.schema import ProviderConfig
from janus.pricing.registry import PricingRegistry
from janus.providers.registry import ProviderRegistry
from janus.routing.auto import (
    AutoStrategy,
    canonical_model_base,
    plan_auto,
    score_candidates,
)
from janus.storage.database import get_connection, init_db
from janus.storage.model_signals import ModelSignals, SignalStats


def _rates(inp: float, out: float) -> dict[str, float]:
    return {
        "input_per_mtok": inp,
        "output_per_mtok": out,
        "cache_creation_per_mtok": 0.0,
        "cache_read_per_mtok": 0.0,
    }


PRICING = PricingRegistry(
    {},
    {
        "cheap-model": _rates(0.1, 0.2),
        "pricey-model": _rates(10.0, 30.0),
        "mid-model": _rates(1.0, 2.0),
        "sibling-a": _rates(0.5, 1.0),
        "sibling-a-001": _rates(0.5, 1.0),
    },
)


def _config(prefix: str, models: list[str]) -> ProviderConfig:
    return ProviderConfig(
        id=prefix,
        prefix=prefix,
        api_type="openai_compat",
        base_url=f"https://{prefix}.example/v1",
        models=models,
        discovered_models=list(models),
    )


def _registry(*configs: ProviderConfig) -> ProviderRegistry:
    registry = ProviderRegistry()
    for config in configs:
        registry.register(config)
    return registry


def _stats(
    *, samples: int = 10, error_rate: float = 0.0, tps: float | None = 50.0, ttft: int | None = 400
) -> SignalStats:
    return SignalStats(
        samples=samples,
        errors=int(error_rate * samples),
        error_rate=error_rate,
        ttft_p50_ms=ttft,
        ttft_p90_ms=ttft,
        tps_p50=tps,
        tps_p90=tps,
        nonstream_tps_p50=None,
        last_seen=None,
        sufficient=samples >= 5,
    )


def test_canonical_model_base_version_suffixes():
    assert canonical_model_base("gemini-2.0-flash-001") == "gemini-2.0-flash"
    assert canonical_model_base("claude-sonnet-4-20250514") == "claude-sonnet-4"
    assert canonical_model_base("claude-opus-4-7") == "claude-opus-4-7"
    assert canonical_model_base("gpt-4o") == "gpt-4o"


def test_cheapest_ranks_by_blended_cost_and_excludes_unpriced():
    registry = _registry(_config("acme", ["cheap-model", "pricey-model", "freebie"]))
    candidates = registry.auto_candidates()
    scored = score_candidates(
        candidates, strategy=AutoStrategy.CHEAPEST, pricing_registry=PRICING, signals=None
    )
    models = [candidate.model_str for candidate in scored]
    assert models == ["acme/cheap-model", "acme/pricey-model", "acme/freebie"]
    assert scored[2].tier == 2
    assert scored[0].blended_cost == pytest.approx(0.3 * 0.1 + 0.7 * 0.2)


def test_quality_override_wins_and_unscored_sink():
    candidates = ["acme/mid-model", "acme/pricey-model"]
    scored = score_candidates(
        candidates,
        strategy=AutoStrategy.QUALITY,
        pricing_registry=PRICING,
        signals=None,
        overrides={"pricey-model": 0.99},
    )
    assert scored[0].model_str == "acme/pricey-model"
    assert scored[0].overridden is True
    assert scored[0].tier == 0
    assert scored[1].tier == 2


def test_quality_ranking_uses_error_rate():
    signals = ModelSignals(
        by_model={"mid-model": _stats(error_rate=0.2), "pricey-model": _stats(error_rate=0.0)},
        by_account={},
    )
    scored = score_candidates(
        ["acme/mid-model", "acme/pricey-model"],
        strategy=AutoStrategy.QUALITY,
        pricing_registry=PRICING,
        signals=signals,
    )
    assert [candidate.model_str for candidate in scored] == [
        "acme/pricey-model",
        "acme/mid-model",
    ]


def test_fastest_requires_both_axes():
    signals = ModelSignals(
        by_model={
            "mid-model": _stats(tps=100.0, ttft=None),
            "pricey-model": _stats(tps=80.0, ttft=200),
        },
        by_account={},
    )
    scored = score_candidates(
        ["acme/mid-model", "acme/pricey-model"],
        strategy=AutoStrategy.FASTEST,
        pricing_registry=PRICING,
        signals=signals,
    )
    assert scored[0].model_str == "acme/pricey-model"
    assert scored[0].tier == 0
    assert scored[1].tier == 2


def test_balanced_mixes_quality_and_cost():
    signals = ModelSignals(
        by_model={
            "mid-model": _stats(error_rate=0.0, tps=50.0, ttft=400),
            "pricey-model": _stats(error_rate=0.0, tps=50.0, ttft=400),
        },
        by_account={},
    )
    scored = score_candidates(
        ["acme/mid-model", "acme/pricey-model"],
        strategy=AutoStrategy.BALANCED,
        pricing_registry=PRICING,
        signals=signals,
    )
    assert scored[0].model_str == "acme/mid-model"


@pytest.mark.asyncio
async def test_plan_auto_dedupe_allowlist_and_top_n(tmp_path):
    db_path = tmp_path / "auto.db"
    await init_db(db_path)
    registry = _registry(
        _config("acme", ["sibling-a-001", "sibling-a", "mid-model", "pricey-model", "cheap-model"])
    )
    plan = await plan_auto(
        registry=registry,
        db_path=db_path,
        pricing_registry=PRICING,
        strategy=AutoStrategy.CHEAPEST,
        top_n=3,
    )
    assert plan.models[0] == "acme/cheap-model"
    bases = [canonical_model_base(model.split("/", 1)[1]) for model in plan.models]
    assert len(bases) == len(set(bases))
    assert len(plan.models) == 3
    assert ("acme/sibling-a-001", "version sibling") in plan.excluded or (
        "acme/sibling-a",
        "version sibling",
    ) in plan.excluded

    restricted = await plan_auto(
        registry=registry,
        db_path=db_path,
        pricing_registry=PRICING,
        strategy=AutoStrategy.CHEAPEST,
        allowed_models=["acme/pricey-model"],
    )
    assert restricted.models == ["acme/pricey-model"]


@pytest.mark.asyncio
async def test_plan_auto_capability_filter(tmp_path):
    db_path = tmp_path / "auto.db"
    await init_db(db_path)
    registry = _registry(_config("acme", ["cheap-model", "pricey-model"]))
    request = CanonicalRequest(
        model="auto",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image", "source": {"type": "url", "url": "https://x.test/a.png"}},
                ],
            }
        ],
    )
    plan = await plan_auto(
        registry=registry,
        db_path=db_path,
        pricing_registry=PRICING,
        request=request,
        strategy=AutoStrategy.CHEAPEST,
    )
    # neither fake model carries the vision capability in model_caps
    assert plan.models == []
    assert all(reason == "capabilities" for _, reason in plan.excluded)


@pytest.mark.asyncio
async def test_resolve_attempts_model_chain_order(tmp_path):
    from janus.routing.fallback import FallbackHandler

    registry = _registry(_config("acme", ["cheap-model", "mid-model"]))
    handler = FallbackHandler(registry)
    attempts = handler.resolve_attempts("auto", model_chain=["acme/cheap-model", "acme/mid-model"])
    assert [attempt.model for attempt in attempts[:2]] == ["cheap-model", "mid-model"]


@pytest.mark.asyncio
async def test_signals_window_query_isolated(tmp_path):
    db_path = tmp_path / "auto.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM attempt_signals")
        await db.execute(
            "INSERT INTO attempt_signals (timestamp, model, account_id, streamed, outcome,"
            " ttft_ms, duration_ms, output_tokens, output_tps)"
            " VALUES (?, ?, ?, 1, 'ok', 300, 900, 100, 42.0)",
            (
                datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(sep=" "),
                "m1",
                "a1",
            ),
        )
        await db.commit()
    plan = await plan_auto(
        registry=_registry(_config("acme", ["cheap-model"])),
        db_path=db_path,
        pricing_registry=PRICING,
        strategy=AutoStrategy.CHEAPEST,
    )
    assert plan.models == ["acme/cheap-model"]
