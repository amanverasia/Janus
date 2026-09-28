"""Probed account-value windows soften fallback ordering (never block)."""

import json
import time
from collections import deque
from datetime import UTC, datetime

from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import AccountStrategy, FallbackHandler


def _config(key_id: str, *, rpm: int | None = None) -> ProviderConfig:
    return ProviderConfig(
        id=f"prov::{key_id}",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-x",
        models=["m1"],
        upstream_key_id=key_id,
        rate_limit_rpm=rpm,
    )


def _handler(configs: list[ProviderConfig]) -> FallbackHandler:
    registry = ProviderRegistry()
    for config in configs:
        registry.register(config)
    return FallbackHandler(registry)


def _order(handler: FallbackHandler) -> list[str]:
    attempts = handler.resolve_attempts("test/m1", strategy=AccountStrategy.FILL_FIRST)
    return [target.account_id for target in attempts]


def test_near_limit_key_ordered_after_fresh_key():
    handler = _handler([_config("uk_spent"), _config("uk_fresh")])
    now = time.time()
    handler._probed_used = {"uk_spent": (95.0, now), "uk_fresh": (10.0, now)}

    assert _order(handler) == ["uk_fresh", "uk_spent"]
    assert handler.last_probe_demotions == [("uk_spent", 95.0)]


def test_exhausted_key_is_last_resort_but_never_blocked():
    handler = _handler([_config("uk_done"), _config("uk_near"), _config("uk_fresh")])
    now = time.time()
    handler._probed_used = {
        "uk_done": (100.0, now),
        "uk_near": (95.0, now),
        "uk_fresh": (1.0, now),
    }

    assert _order(handler) == ["uk_fresh", "uk_near", "uk_done"]
    assert len(handler.resolve_attempts("test/m1")) == 3


def test_stale_probe_data_is_neutral():
    handler = _handler([_config("uk_spent"), _config("uk_fresh")])
    stale = time.time() - 700.0
    handler._probed_used = {"uk_spent": (95.0, stale), "uk_fresh": (10.0, stale)}

    assert _order(handler) == ["uk_spent", "uk_fresh"]
    assert handler.last_probe_demotions == []


def test_absent_probe_data_is_neutral():
    handler = _handler([_config("uk_a"), _config("uk_b")])

    assert _order(handler) == ["uk_a", "uk_b"]
    assert handler.last_probe_demotions == []


def test_rate_limit_demotion_dominates_probed_headroom():
    handler = _handler([_config("uk_probed", rpm=10), _config("uk_rl", rpm=1)])
    now = time.time()
    handler._probed_used = {"uk_probed": (95.0, now)}
    handler._request_times["uk_rl"] = deque([now, now])

    assert _order(handler) == ["uk_probed", "uk_rl"]


def test_non_inventory_accounts_stay_neutral():
    handler = _handler(
        [
            _config("uk_a"),
            ProviderConfig(
                id="plain",
                prefix="test",
                api_type="openai_compat",
                base_url="https://fake.local/v1",
                api_key="sk-y",
                models=["m1"],
            ),
        ]
    )
    handler._probed_used = {"uk_a": (99.0, time.time())}

    order = _order(handler)
    assert order == ["plain", "uk_a"]
    assert handler.last_probe_demotions == [("uk_a", 99.0)]


async def test_load_probed_headroom_seeds_from_db(tmp_path):
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key, update_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    spent = await create_upstream_key(db_path, provider_id="zhipu", key_value="sk-spent")
    fresh = await create_upstream_key(db_path, provider_id="zhipu", key_value="sk-fresh")
    now_iso = datetime.now(UTC).isoformat()
    for key, percent in ((spent, 96.0), (fresh, 12.0)):
        await update_upstream_key(
            db_path,
            key["id"],
            {
                "account_value": json.dumps(
                    {
                        "status": "ok",
                        "windows": [{"label": "5h", "used_percent": percent}],
                    }
                ),
                "account_value_status": "ok",
                "account_value_checked_at": now_iso,
            },
        )

    handler = _handler([_config(str(spent["id"])), _config(str(fresh["id"]))])
    handler.db_path = db_path
    await handler.load_probed_headroom()

    assert handler._probed_used[str(spent["id"])][0] == 96.0
    assert _order(handler) == [str(fresh["id"]), str(spent["id"])]


async def test_load_probed_headroom_skips_stale_and_unavailable(tmp_path):
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key, update_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    stale = await create_upstream_key(db_path, provider_id="zhipu", key_value="sk-stale")
    broken = await create_upstream_key(db_path, provider_id="zhipu", key_value="sk-broken")
    old_iso = datetime.fromtimestamp(time.time() - 900.0, tz=UTC).isoformat()
    await update_upstream_key(
        db_path,
        stale["id"],
        {
            "account_value": json.dumps(
                {"status": "ok", "windows": [{"label": "5h", "used_percent": 99.0}]}
            ),
            "account_value_status": "ok",
            "account_value_checked_at": old_iso,
        },
    )
    await update_upstream_key(
        db_path,
        broken["id"],
        {
            "account_value": json.dumps(
                {"status": "ok", "windows": [{"label": "5h", "used_percent": 99.0}]}
            ),
            "account_value_status": "unavailable",
            "account_value_checked_at": datetime.now(UTC).isoformat(),
        },
    )

    handler = _handler([_config(str(stale["id"])), _config(str(broken["id"]))])
    handler.db_path = db_path
    await handler.load_probed_headroom()

    assert handler._probed_used == {}
    assert _order(handler) == [str(stale["id"]), str(broken["id"])]
