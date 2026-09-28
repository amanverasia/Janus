from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from janus.inventory.account_value import (
    _ZAI_QUOTA_PATH,
    REFRESH_TTL_SECONDS,
    AccountValue,
    AccountValueStatus,
    ProbeError,
    UsageWindow,
    probe_account_value,
    refresh_account_value,
    worst_window_percent,
)
from janus.inventory.url_guard import BlockedUrlError


def _ok(body: dict) -> Response:
    return Response(200, json=body)


@pytest.fixture(autouse=True)
def _allow_private_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    from janus.inventory import url_guard

    monkeypatch.setattr(url_guard, "_allow_private", lambda: True)


@respx.mock
async def test_openrouter_probe_parses_credits() -> None:
    respx.get("https://openrouter.ai/api/v1/key").mock(
        return_value=_ok(
            {
                "data": {
                    "limit": 100.0,
                    "limit_remaining": 42.5,
                    "usage": 10.0,
                    "is_free_tier": False,
                }
            }
        )
    )
    value = await probe_account_value("openrouter", "sk-or", "https://openrouter.ai/api/v1", None)
    assert value.status is AccountValueStatus.OK
    assert value.credits is not None
    assert value.credits.remaining == 42.5
    assert value.credits.total == 100.0
    assert value.credits.used == pytest.approx(57.5)
    assert value.metadata == {"is_free_tier": False}


@respx.mock
async def test_deepseek_probe_prefers_usd_row() -> None:
    respx.get("https://api.deepseek.com/user/balance").mock(
        return_value=_ok(
            {
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "100.00",
                        "granted_balance": "20.00",
                        "topped_up_balance": "80.00",
                    },
                    {
                        "currency": "USD",
                        "total_balance": "50.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "40.00",
                    },
                ]
            }
        )
    )
    value = await probe_account_value("deepseek", "sk", "https://api.deepseek.com/v1", None)
    assert value.credits is not None
    assert value.credits.remaining == 50.0
    assert value.credits.total == 50.0
    assert value.credits.used == 0.0
    assert value.metadata == {}


@respx.mock
async def test_deepseek_cny_row_converts_to_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVENTORY_CNY_USD_RATE", "0.1")
    respx.get("https://api.deepseek.com/user/balance").mock(
        return_value=_ok(
            {
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "9558.21",
                        "granted_balance": "10000.00",
                        "topped_up_balance": "0.00",
                    }
                ]
            }
        )
    )
    value = await probe_account_value("deepseek", "sk", "https://api.deepseek.com/v1", None)
    assert value.credits is not None
    assert value.credits.remaining == pytest.approx(955.82)
    assert value.credits.total == pytest.approx(1000.0)
    assert value.metadata["credits_currency"] == "CNY"


@respx.mock
async def test_moonshot_cn_host_converts_cny() -> None:
    respx.get("https://api.moonshot.cn/v1/users/me/balance").mock(
        return_value=_ok(
            {
                "code": 0,
                "data": {
                    "available_balance": "700.00",
                    "cash_balance": "1000.00",
                    "voucher_balance": "100.00",
                },
            }
        )
    )
    value = await probe_account_value("moonshot", "sk", "https://api.moonshot.cn/v1", None)
    assert value.credits is not None
    assert value.credits.currency == "USD"
    assert value.metadata["credits_currency"] == "CNY"
    assert value.metadata["voucher_balance"] == 100.0


@respx.mock
async def test_zhipu_limits_decode_windows() -> None:
    route = respx.get(f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=_ok(
            {
                "success": True,
                "data": {
                    "limits": [
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 3,
                            "number": 5,
                            "percentage": 12.5,
                            "nextResetTime": 1750000000000,
                        },
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 6,
                            "number": 1,
                            "currentValue": 300,
                            "usage": 400,
                        },
                        {"type": "TIME_LIMIT", "unit": 6, "number": 1, "percentage": 99},
                    ]
                },
            }
        )
    )
    value = await probe_account_value(
        "zhipu", "hex.secret", "https://open.bigmodel.cn/api/paas/v4", None
    )
    assert route.called
    assert [w.label for w in value.windows] == ["5h", "weekly"]
    assert value.windows[0].used_percent == 12.5
    assert value.windows[0].reset_at is not None
    assert value.windows[1].used_percent == 75.0
    assert value.windows[1].reset_at is None


@respx.mock
async def test_zhipu_bigmodel_uses_bare_key_and_falls_back_to_zai() -> None:
    bigmodel = respx.get(f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=Response(401)
    )
    zai = respx.get(f"https://api.z.ai{_ZAI_QUOTA_PATH}").mock(
        return_value=_ok(
            {
                "data": {
                    "limits": [{"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 40}]
                }
            }
        )
    )
    value = await probe_account_value(
        "zhipu", "hex.secret", "https://open.bigmodel.cn/api/paas/v4", None
    )
    assert bigmodel.called and zai.called
    request = zai.calls[0].request
    assert request.headers["Authorization"] == "Bearer hex.secret"
    assert value.windows[0].used_percent == 40.0


@respx.mock
async def test_zhipu_authoritative_empty_limits_clears_windows() -> None:
    respx.get(f"https://open.bigmodel.cn{_ZAI_QUOTA_PATH}").mock(
        return_value=_ok(
            {"data": {"limits": [{"type": "TIME_LIMIT", "unit": 6, "number": 1, "percentage": 50}]}}
        )
    )
    value = await probe_account_value(
        "zhipu", "hex.secret", "https://open.bigmodel.cn/api/paas/v4", None
    )
    assert value.status is AccountValueStatus.OK
    assert value.windows == []


@respx.mock
async def test_zhipu_legacy_fields_fallback() -> None:
    respx.get("https://api.z.ai/api/monitor/usage/quota/limit").mock(
        return_value=_ok({"data": {"fiveHourUsage": 20, "weeklyUsed": 80}})
    )
    value = await probe_account_value("zhipu", "hex.secret", "https://api.z.ai/api/paas/v4", None)
    assert [w.label for w in value.windows] == ["5h", "weekly"]
    assert value.windows[1].used_percent == 80.0


@respx.mock
async def test_minimax_windows() -> None:
    respx.get("https://api.minimax.io/v1/api/openplatform/coding_plan/remains").mock(
        return_value=_ok(
            {
                "base_resp": {"status_code": 0},
                "model_remains": [
                    {
                        "model_name": "general",
                        "current_interval_remaining_percent": 30,
                        "end_time": 1750000000,
                        "current_weekly_status": 1,
                        "current_weekly_remaining_percent": 10,
                        "weekly_end_time": 1750086400,
                    },
                    {"model_name": "video", "current_interval_remaining_percent": 99},
                ],
            }
        )
    )
    value = await probe_account_value("minimax", "sk", "https://api.minimax.io/v1", None)
    assert [w.label for w in value.windows] == ["Coding Plan 5-hour", "Coding Plan weekly"]
    assert value.windows[0].used_percent == 70.0
    assert value.windows[1].used_percent == 90.0


@respx.mock
async def test_venice_prefers_usd_balance() -> None:
    respx.get("https://api.venice.ai/api/v1/billing/balance").mock(
        return_value=_ok({"data": {"balance": 500.0, "balance_usd": 25.5}})
    )
    value = await probe_account_value("venice", "sk", "https://api.venice.ai/api/v1", None)
    assert value.credits is not None
    assert value.credits.remaining == 25.5
    assert value.metadata == {"diem_balance": 500.0}


@respx.mock
async def test_venice_epoch_window() -> None:
    respx.get("https://api.venice.ai/api/v1/billing/balance").mock(
        return_value=_ok(
            {"data": {"balance_usd": 1.0, "diem_epoch_used": 25, "diem_epoch_allocated": 100}}
        )
    )
    value = await probe_account_value("venice", "sk", "https://api.venice.ai/api/v1", None)
    assert value.windows[0].used_percent == 25.0


async def test_unsupported_provider() -> None:
    value = await probe_account_value("openai", "sk", "https://api.openai.com/v1", None)
    assert value.status is AccountValueStatus.UNSUPPORTED


@respx.mock
async def test_terminal_auth_failure_is_not_transient() -> None:
    respx.get("https://openrouter.ai/api/v1/key").mock(return_value=Response(401))
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("openrouter", "sk", "https://openrouter.ai/api/v1", None)
    assert excinfo.value.transient is False


@respx.mock
async def test_server_error_is_transient() -> None:
    respx.get("https://api.deepseek.com/user/balance").mock(return_value=Response(503))
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("deepseek", "sk", "https://api.deepseek.com", None)
    assert excinfo.value.transient is True


async def test_blocked_url_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    from janus.inventory import url_guard

    monkeypatch.setattr(url_guard, "_allow_private", lambda: False)
    with pytest.raises(BlockedUrlError):
        await probe_account_value("openrouter", "sk", "http://127.0.0.1:9/v1", None)


def test_worst_window_percent_and_roundtrip() -> None:
    value = AccountValue(
        status=AccountValueStatus.OK,
        source="zhipu:quota-limit",
        fetched_at="2026-09-28T00:00:00+00:00",
        windows=[
            UsageWindow(label="5h", used_percent=40),
            UsageWindow(label="weekly", used_percent=130),
        ],
    )
    assert worst_window_percent(value) == 100.0
    assert value.to_dict()["windows"][1]["used_percent"] == 100.0
    restored = AccountValue.from_dict(value.to_dict())
    assert restored == value


async def test_refresh_account_value_roundtrip(tmp_path) -> None:
    from janus.inventory import account_value as module
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key, get_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    created = await create_upstream_key(
        str(db_path), provider_id="zhipu", key_value="a" * 32 + "." + "b" * 16
    )
    key_id = created["id"]

    async def fake_probe(provider_id, key_value, base_url, custom_base_url):
        return AccountValue(
            status=AccountValueStatus.OK,
            source="zhipu:quota-limit",
            fetched_at="2026-09-28T00:00:00+00:00",
            windows=[module.UsageWindow(label="5h", used_percent=42.0, reset_at=None)],
        )

    original = module.probe_account_value
    module.probe_account_value = fake_probe
    try:
        state = await refresh_account_value(str(db_path), key_id)
    finally:
        module.probe_account_value = original
    assert state is not None
    assert state["status"] == "ok"
    assert state["value"]["windows"][0]["used_percent"] == 42.0

    stored = await get_upstream_key(str(db_path), key_id)
    assert isinstance(stored["account_value"], dict)
    assert stored["account_value_status"] == "ok"

    second = await refresh_account_value(str(db_path), key_id)
    checked_at = datetime.fromisoformat(str(second["checked_at"]))
    assert datetime.now(UTC) - checked_at < timedelta(seconds=REFRESH_TTL_SECONDS)
    assert json.dumps(state["value"]) == json.dumps(second["value"])


async def test_refresh_preserves_last_good_on_failure(tmp_path) -> None:
    from janus.inventory import account_value as module
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    created = await create_upstream_key(str(db_path), provider_id="venice", key_value="venice-key")
    key_id = created["id"]

    good = AccountValue(
        status=AccountValueStatus.OK,
        source="venice:billing-balance",
        fetched_at="2026-09-28T00:00:00+00:00",
        credits=module.CreditValue(remaining=9.0, currency="USD"),
    )

    async def good_probe(provider_id, key_value, base_url, custom_base_url):
        return good

    original = module.probe_account_value
    module.probe_account_value = good_probe
    try:
        await refresh_account_value(str(db_path), key_id, force=True)
    finally:
        module.probe_account_value = original

    async def bad_probe(provider_id, key_value, base_url, custom_base_url):
        raise ProbeError("HTTP 503 from quota endpoint", transient=True)

    module.probe_account_value = bad_probe
    try:
        state = await refresh_account_value(str(db_path), key_id, force=True)
    finally:
        module.probe_account_value = original

    assert state["status"] == "unavailable"
    assert state["value"]["credits"]["remaining"] == 9.0
    assert state["fetched_at"] == "2026-09-28T00:00:00+00:00"
    assert "503" in str(state["error"])


async def test_refresh_unsupported_provider_records_status(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    created = await create_upstream_key(str(db_path), provider_id="groq", key_value="gsk")
    state = await refresh_account_value(str(db_path), created["id"])
    assert state["status"] == "unsupported"


async def test_refresh_missing_key_returns_none(tmp_path) -> None:
    from janus.storage.database import init_db

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    assert await refresh_account_value(str(db_path), "nope") is None
