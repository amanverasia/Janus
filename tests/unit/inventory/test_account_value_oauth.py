from __future__ import annotations

import asyncio
import json
import time

import pytest
import respx
from httpx import Response

from janus.inventory.account_value import (
    ACCOUNT_VALUE_PROBES,
    AccountValueStatus,
    ProbeError,
    probe_account_value,
    refresh_account_value,
)

CODEX_URL = "https://chatgpt.com/backend-api/wham/usage"
KIRO_URL = "https://management.eu-central-1.kiro.dev/"
ANTIGRAVITY_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"


@pytest.fixture(autouse=True)
def _allow_private_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    from janus.inventory import url_guard

    monkeypatch.setattr(url_guard, "_allow_private", lambda: True)


def _cred(**fields: object) -> str:
    base: dict[str, object] = {
        "access_token": "at-live",
        "refresh_token": "rt-secret",
        "expires_at": time.time() + 3600,
    }
    base.update(fields)
    return json.dumps(base)


def test_oauth_providers_registered() -> None:
    for provider_id in ("codex", "kiro", "antigravity"):
        assert provider_id in ACCOUNT_VALUE_PROBES


@respx.mock
async def test_codex_probe_maps_windows_by_duration() -> None:
    route = respx.get(CODEX_URL).mock(
        return_value=Response(
            200,
            json={
                "plan_type": "plus",
                "email": "person@example.com",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 37,
                        "limit_window_seconds": 18000,
                        "reset_at": 1790000000,
                    },
                    "secondary_window": {
                        "used_percent": 81.5,
                        "limit_window_seconds": 604800,
                        "reset_at": 1790500000,
                    },
                    "tertiary_window": None,
                },
                "rate_limit_reset_credits": {"available_count": 2},
            },
        )
    )
    value = await probe_account_value(
        "codex", _cred(extra={"workspaceId": "ws-1"}), "https://chatgpt.com/backend-api", None
    )
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer at-live"
    assert request.headers["ChatGPT-Account-Id"] == "ws-1"
    assert value.status is AccountValueStatus.OK
    assert [(w.label, w.used_percent) for w in value.windows] == [("5h", 37.0), ("weekly", 81.5)]
    assert value.windows[0].reset_at is not None
    assert value.metadata == {"plan_type": "plus", "reset_credits_available": 2}
    assert "person@example.com" not in json.dumps(value.to_dict())


@respx.mock
async def test_codex_probe_without_duration_uses_slot_name() -> None:
    respx.get(CODEX_URL).mock(
        return_value=Response(200, json={"rate_limit": {"primary_window": {"used_percent": 10}}})
    )
    value = await probe_account_value("codex", "bare-access-token", "", None)
    assert [(w.label, w.used_percent) for w in value.windows] == [("primary", 10.0)]


@respx.mock
async def test_codex_probe_empty_response_is_transient_error() -> None:
    respx.get(CODEX_URL).mock(return_value=Response(200, json={}))
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("codex", _cred(), "", None)
    assert excinfo.value.transient is True


@respx.mock
async def test_oauth_probe_auth_failure_is_terminal() -> None:
    respx.get(CODEX_URL).mock(return_value=Response(401))
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("codex", _cred(), "", None)
    assert excinfo.value.transient is False
    assert "401" in str(excinfo.value)
    assert "at-live" not in str(excinfo.value)


@respx.mock
async def test_oauth_probe_skips_expired_token_without_network() -> None:
    route = respx.get(CODEX_URL).mock(return_value=Response(200, json={}))
    with pytest.raises(ProbeError, match="expired"):
        await probe_account_value("codex", _cred(expires_at=time.time() - 10), "", None)
    with pytest.raises(ProbeError, match="expired"):
        await probe_account_value("codex", _cred(expires_at=(time.time() - 10) * 1000), "", None)
    assert route.call_count == 0


async def test_oauth_probe_requires_access_token() -> None:
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("kiro", json.dumps({"refresh_token": "rt"}), "", None)
    assert excinfo.value.transient is False


@respx.mock
async def test_kiro_probe_parses_usage_breakdown() -> None:
    arn = "arn:aws:codewhisperer:eu-central-1:123456789012:profile/ABC"
    route = respx.post(KIRO_URL).mock(
        return_value=Response(
            200,
            json={
                "nextDateReset": 1791000000,
                "userInfo": {"email": "person@example.com", "userId": "u-1"},
                "overageConfiguration": {"overageStatus": "ENABLED"},
                "usageBreakdownList": [
                    {"resourceType": "CREDIT", "currentUsage": 1, "usageLimit": 2},
                    {
                        "resourceType": "AGENTIC_REQUEST",
                        "currentUsage": 695,
                        "currentUsageWithPrecision": 695.17,
                        "usageLimit": 1000,
                        "freeTrialInfo": {"currentUsage": 25, "usageLimit": 100},
                    },
                ],
            },
        )
    )
    value = await probe_account_value("kiro", _cred(extra={"profileArn": arn}), "", None)
    request = route.calls.last.request
    assert request.headers["x-amz-target"] == "AmazonCodeWhispererService.GetUsageLimits"
    assert request.url.params["profileArn"] == arn
    assert json.loads(request.content)["profileArn"] == arn
    assert [(w.label, w.used_percent) for w in value.windows] == [
        ("monthly", pytest.approx(69.517)),
        ("Free trial", 25.0),
    ]
    assert value.windows[0].reset_at is not None
    assert value.metadata["resource_type"] == "AGENTIC_REQUEST"
    assert value.metadata["overage_enabled"] is True
    assert "person@example.com" not in json.dumps(value.to_dict())


@respx.mock
async def test_kiro_probe_rejects_unsafe_region() -> None:
    route = respx.post("https://management.us-east-1.kiro.dev/").mock(
        return_value=Response(
            200,
            json={
                "usageBreakdownList": [
                    {"resourceType": "CREDIT", "currentUsage": 5, "usageLimit": 50}
                ]
            },
        )
    )
    value = await probe_account_value(
        "kiro", _cred(extra={"region": "evil.example.com/x"}), "", None
    )
    assert route.call_count == 1
    assert value.windows[0].used_percent == 10.0


@respx.mock
async def test_kiro_probe_unknown_bucket_is_transient() -> None:
    respx.post("https://management.us-east-1.kiro.dev/").mock(
        return_value=Response(200, json={"usageBreakdownList": [{"resourceType": "OTHER"}]})
    )
    with pytest.raises(ProbeError) as excinfo:
        await probe_account_value("kiro", _cred(), "", None)
    assert excinfo.value.transient is True


@respx.mock
async def test_antigravity_probe_parses_groups() -> None:
    route = respx.post(ANTIGRAVITY_URL).mock(
        return_value=Response(
            200,
            json={
                "groups": [
                    {
                        "displayName": "Gemini models",
                        "buckets": [
                            {
                                "window": "5h",
                                "remainingFraction": 0.75,
                                "resetTime": "2026-10-01T12:00:00Z",
                            },
                            {"window": "weekly", "remaining": {"remainingFraction": 0.1}},
                        ],
                    },
                    {
                        "displayName": "Claude and 3P models",
                        "buckets": [{"bucketId": "five-hour", "remainingFraction": 1}],
                    },
                    {"displayName": "Images", "buckets": [{"remainingFraction": 0.5}]},
                ]
            },
        )
    )
    value = await probe_account_value("antigravity", _cred(extra={"projectId": "proj-1"}), "", None)
    assert json.loads(route.calls.last.request.content) == {"project": "proj-1"}
    assert [(w.label, w.used_percent) for w in value.windows] == [
        ("Gemini 5h", 25.0),
        ("Gemini weekly", pytest.approx(90.0)),
        ("Claude 5h", 0.0),
        ("Images", 50.0),
    ]
    assert value.windows[0].reset_at is not None


async def test_antigravity_probe_requires_project() -> None:
    with pytest.raises(ProbeError, match="projectId") as excinfo:
        await probe_account_value("antigravity", _cred(), "", None)
    assert excinfo.value.transient is False


async def test_auth_failure_marks_probe_unavailable_without_invalidating_key(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key, get_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    created = await create_upstream_key(str(db_path), provider_id="codex", key_value=_cred())
    before = await get_upstream_key(str(db_path), created["id"])

    with respx.mock:
        respx.get(CODEX_URL).mock(return_value=Response(403))
        state = await refresh_account_value(str(db_path), created["id"], force=True)

    after = await get_upstream_key(str(db_path), created["id"])
    assert state is not None
    assert state["status"] == "unavailable"
    assert "403" in str(state["error"])
    assert after["status"] == before["status"]
    assert after["key_value"] == before["key_value"]


async def test_concurrent_oauth_refreshes_share_one_probe(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import create_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    created = await create_upstream_key(str(db_path), provider_id="codex", key_value=_cred())

    async def slow(request: object) -> Response:
        await asyncio.sleep(0.05)
        return Response(200, json={"rate_limit": {"primary_window": {"used_percent": 12}}})

    with respx.mock:
        route = respx.get(CODEX_URL).mock(side_effect=slow)
        states = await asyncio.gather(
            *(refresh_account_value(str(db_path), created["id"], force=True) for _ in range(5))
        )
    assert route.call_count == 1
    assert all(state is not None and state["status"] == "ok" for state in states)
