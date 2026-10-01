from __future__ import annotations

import datetime

import pytest

from janus.pricing.registry import PricingRegistry
from janus.storage.database import get_connection, init_db
from janus.storage.savings import (
    build_savings,
    savings_for_days,
    savings_for_today,
)
from janus.storage.settings import (
    resolve_savings_baseline,
    validate_savings_baseline,
)
from janus.storage.usage import get_savings_window_totals, record_usage

REGISTRY = PricingRegistry({}, {})


def _totals(included: list[dict], subscription: dict | None = None, unpriced: dict | None = None):
    return {
        "included": included,
        "subscription": subscription or {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        "unpriced": unpriced or {"requests": 0, "input_tokens": 0, "output_tokens": 0},
    }


def test_build_savings_uses_compute_cost_math():
    totals = _totals(
        [
            {
                "model": "deepseek-chat",
                "requests": 2,
                "input_tokens": 1_000_000,
                "output_tokens": 500_000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cost": 1.0,
            }
        ]
    )
    payload = build_savings(totals, baseline="gpt-4o", registry=REGISTRY)
    pricing = REGISTRY.get("gpt-4o")
    assert pricing is not None
    expected_baseline = 1.0 * pricing.input_per_mtok + 0.5 * pricing.output_per_mtok
    assert payload["baseline_priced"] is True
    assert payload["actual_cost"] == pytest.approx(1.0)
    assert payload["baseline_cost"] == pytest.approx(expected_baseline)
    assert payload["savings"] == pytest.approx(expected_baseline - 1.0)
    assert payload["savings_pct"] == pytest.approx(
        (expected_baseline - 1.0) / expected_baseline * 100, abs=0.01
    )
    assert payload["by_model"] == [
        {
            "model": "deepseek-chat",
            "requests": 2,
            "actual_cost": pytest.approx(1.0),
            "baseline_cost": pytest.approx(expected_baseline),
            "savings": pytest.approx(expected_baseline - 1.0),
        }
    ]


def test_build_savings_cache_tokens_are_subsets():
    totals = _totals(
        [
            {
                "model": "m",
                "requests": 1,
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_creation_tokens": 200_000,
                "cache_read_tokens": 300_000,
                "cost": 0.0,
            }
        ]
    )
    payload = build_savings(totals, baseline="gpt-4o", registry=REGISTRY)
    pricing = REGISTRY.get("gpt-4o")
    assert pricing is not None
    expected = (
        0.5 * pricing.input_per_mtok
        + 0.2 * pricing.cache_creation_per_mtok
        + 0.3 * pricing.cache_read_per_mtok
    )
    assert payload["baseline_cost"] == pytest.approx(expected)


def test_build_savings_unpriced_baseline_reports_zero():
    totals = _totals(
        [
            {
                "model": "m",
                "requests": 1,
                "input_tokens": 100,
                "output_tokens": 100,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cost": 0.01,
            }
        ]
    )
    payload = build_savings(totals, baseline="totally-unknown-model", registry=REGISTRY)
    assert payload["baseline_priced"] is False
    assert payload["baseline_cost"] == 0.0
    assert payload["savings"] == pytest.approx(-0.01)
    assert payload["savings_pct"] == 0.0
    assert payload["by_model"] == []


def test_build_savings_by_model_capped_and_sorted():
    rows = []
    for i in range(30):
        rows.append(
            {
                "model": f"m{i:02d}",
                "requests": 1,
                "input_tokens": 10_000,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cost": float(i),
            }
        )
    payload = build_savings(_totals(rows), baseline="gpt-4o", registry=REGISTRY)
    assert len(payload["by_model"]) == 25
    savings_values = [row["savings"] for row in payload["by_model"]]
    assert savings_values == sorted(savings_values, reverse=True)


def test_resolve_and_validate_savings_baseline():
    assert resolve_savings_baseline({}) == "gpt-4o"
    assert resolve_savings_baseline({"analytics_savings_baseline": "claude-sonnet-4-5"}) == (
        "claude-sonnet-4-5"
    )
    assert resolve_savings_baseline({"analytics_savings_baseline": "   "}) == "gpt-4o"
    assert validate_savings_baseline("  gpt-4o ") == "gpt-4o"
    with pytest.raises(ValueError):
        validate_savings_baseline("")
    with pytest.raises(ValueError):
        validate_savings_baseline("x" * 101)


@pytest.mark.asyncio
async def test_get_savings_window_totals_buckets(tmp_path):
    db_path = tmp_path / "s.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO providers (id, prefix, api_type, base_url, api_key, is_enabled) "
            "VALUES ('codex-1', 'codex', 'codex', 'https://x.test', 'enc', 1)"
        )
        await db.commit()
    await record_usage(
        db_path, provider_id="p1", model="m1", input_tokens=100, output_tokens=50, cost=0.2
    )
    await record_usage(
        db_path, provider_id="p1", model="m1", input_tokens=100, output_tokens=50, cost=0.2
    )
    await record_usage(
        db_path, provider_id="p1", model="unpriced", input_tokens=10, output_tokens=5, cost=0.0
    )
    await record_usage(
        db_path, provider_id="codex-1", model="gpt-5", input_tokens=999, output_tokens=99, cost=0.0
    )
    totals = await get_savings_window_totals(
        db_path, start="2000-01-01 00:00:00", end="2999-01-01 00:00:00"
    )
    assert [row["model"] for row in totals["included"]] == ["m1"]
    included = totals["included"][0]
    assert included["requests"] == 2
    assert included["input_tokens"] == 200
    assert included["cost"] == pytest.approx(0.4)
    assert totals["unpriced"]["requests"] == 1
    assert totals["subscription"]["requests"] == 1


@pytest.mark.asyncio
async def test_savings_for_days_excludes_subscription_and_unpriced(tmp_path):
    db_path = tmp_path / "s.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO providers (id, prefix, api_type, base_url, api_key, is_enabled) "
            "VALUES ('codex-1', 'codex', 'codex', 'https://x.test', 'enc', 1)"
        )
        await db.commit()
    await record_usage(
        db_path,
        provider_id="p1",
        model="deepseek-chat",
        input_tokens=50_000,
        output_tokens=25_000,
        cost=0.05,
    )
    await record_usage(
        db_path, provider_id="p1", model="freebie", input_tokens=1000, output_tokens=0, cost=0.0
    )
    await record_usage(
        db_path, provider_id="codex-1", model="gpt-5", input_tokens=1000, output_tokens=0, cost=0.0
    )
    payload = await savings_for_days(db_path, baseline="gpt-4o", registry=REGISTRY, days=30)
    assert payload["baseline"] == "gpt-4o"
    assert payload["requests"] == 1
    assert payload["excluded"] == {"subscription_requests": 1, "unpriced_requests": 1}
    assert payload["window"] == {"kind": "days", "days": 30}
    assert payload["actual_cost"] == pytest.approx(0.05)
    assert payload["savings"] > 0


@pytest.mark.asyncio
async def test_savings_for_today_uses_reporting_calendar_day(tmp_path):
    db_path = tmp_path / "s.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('server_reporting_timezone', 'Asia/Kolkata')"
        )
        await db.commit()
    now = datetime.datetime(2026, 10, 1, 20, 30, tzinfo=datetime.UTC)
    from tests.fixtures.usage_seed import seed_usage

    await seed_usage(
        db_path,
        [
            {
                "timestamp": "2026-10-01 19:00:00",
                "provider_id": "p1",
                "model": "m1",
                "input_tokens": 1000,
                "cost": 0.01,
            }
        ],
    )
    payload = await savings_for_today(db_path, baseline="gpt-4o", registry=REGISTRY, now=now)
    assert payload["window"] == {"kind": "today", "reporting_timezone": "Asia/Kolkata"}
    assert "by_model" not in payload
    assert payload["requests"] == 1
