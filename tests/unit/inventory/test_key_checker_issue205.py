"""Regression tests for issue #205: validators must not flip healthy keys invalid."""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from janus.inventory import key_checker
from janus.inventory.key_checker import (
    ANTIGRAVITY_LOAD_CODE_ASSIST_URL,
    ANTIGRAVITY_ONBOARD_URL,
    FETCH_TIMEOUT,
    _extract_rate_limits,
    validate_key,
)
from janus.providers.base import RawResult
from janus.providers.kiro import KiroProvider
from tests.fixtures.url_mock import mocked_route

# ---------------------------------------------------------------------------
# 1. Tolerant x-ratelimit-* header parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("500", 500),
        ("1,000", 1000),
        ("500.0", 500),
        (" 60 ", 60),
        ("unlimited", None),
        ("-", None),
    ],
)
def test_extract_rate_limits_tolerates_non_integer_values(raw: str, expected: int | None) -> None:
    headers = {
        "x-ratelimit-limit-requests": raw,
        "x-ratelimit-limit-tokens": raw,
        "x-ratelimit-requests-per-day": raw,
    }
    result = _extract_rate_limits(headers)
    assert result == {"rpm": expected, "tpm": expected, "rpd": expected}


def test_extract_rate_limits_unparseable_specific_header_falls_back_to_combined() -> None:
    result = _extract_rate_limits(
        {
            "x-ratelimit-limit-requests": "n/a",
            "x-ratelimit-limit": "100 req/min, 20000 tok/min",
        }
    )
    assert result["rpm"] == 100
    assert result["tpm"] == 20000


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_non_integer_ratelimit_header_keeps_key_valid() -> None:
    mocked_route("GET", "https://api.openai.com/v1/models").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "gpt-4o"}]},
            headers={
                "x-ratelimit-limit-requests": "1,000",
                "x-ratelimit-limit-tokens": "500.0",
            },
        )
    )
    result = await validate_key("sk-proj-test", "openai", skip_probe=True)
    assert result["is_valid"] is True
    assert result["rate_limit_rpm"] == 1000
    assert result["rate_limit_tpm"] == 500


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_garbage_ratelimit_header_on_429_keeps_key_valid() -> None:
    mocked_route("GET", "https://api.openai.com/v1/models").mock(
        return_value=Response(429, json={}, headers={"x-ratelimit-limit-requests": "lots"})
    )
    result = await validate_key("sk-proj-test", "openai", skip_probe=True)
    assert result["is_valid"] is True
    assert result["partial_check"] is True
    assert "rate_limit_rpm" not in result


# ---------------------------------------------------------------------------
# 2. Kiro transient failures are inconclusive, not invalid
# ---------------------------------------------------------------------------


def _patch_kiro_call(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    async def fake_call(self: KiroProvider, payload: dict[str, Any], stream: bool = False):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(KiroProvider, "call", fake_call)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_kiro_5xx_is_probe_inconclusive(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _patch_kiro_call(monkeypatch, RawResult(status_code=status, json_data={"error": "x"}))
    result = await validate_key("kiro-api-key", "kiro")
    assert result.get("probe_inconclusive") is True
    assert result.get("is_valid") is not False
    assert str(status) in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_kiro_network_error_is_probe_inconclusive(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    _patch_kiro_call(monkeypatch, exc)
    result = await validate_key("kiro-api-key", "kiro")
    assert result.get("probe_inconclusive") is True
    assert result.get("is_valid") is not False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_kiro_auth_failure_still_invalid(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    _patch_kiro_call(monkeypatch, RawResult(status_code=status, json_data={}))
    result = await validate_key("kiro-api-key", "kiro")
    assert result["is_valid"] is False
    assert not result.get("probe_inconclusive")


@pytest.mark.asyncio
async def test_kiro_other_4xx_still_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_kiro_call(monkeypatch, RawResult(status_code=400, json_data={}))
    result = await validate_key("kiro-api-key", "kiro")
    assert result["is_valid"] is False
    assert not result.get("probe_inconclusive")


# ---------------------------------------------------------------------------
# 3. Antigravity onboarding wait is deadline-bounded
# ---------------------------------------------------------------------------


def test_antigravity_onboard_budget_is_fetch_timeout_scale() -> None:
    assert 0 < key_checker.ANTIGRAVITY_ONBOARD_MAX_WAIT <= FETCH_TIMEOUT


@pytest.mark.asyncio
@respx.mock
async def test_antigravity_onboarding_wait_is_bounded_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(key_checker, "ANTIGRAVITY_ONBOARD_MAX_WAIT", 0.2, raising=False)
    respx.post(ANTIGRAVITY_LOAD_CODE_ASSIST_URL).mock(
        return_value=httpx.Response(200, json={"allowedTiers": []})
    )
    onboard = respx.post(ANTIGRAVITY_ONBOARD_URL).mock(
        return_value=httpx.Response(200, json={"done": False})
    )

    # Pre-fix this loop sleeps 10 x 5s (~45s); it must finish near the budget.
    result = await asyncio.wait_for(
        validate_key(json.dumps({"access_token": "access-token"}), "antigravity"),
        timeout=3,
    )
    assert result["is_valid"] is True
    assert result["usability_note"] == "OAuth valid; onboarding timed out"
    assert onboard.called
