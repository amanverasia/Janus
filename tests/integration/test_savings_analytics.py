from __future__ import annotations

import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.api_keys import create_key
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth
from tests.fixtures.usage_seed import seed_usage

pytestmark = pytest.mark.asyncio

HEADERS = {"Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}", "Accept": "application/json"}


@pytest.fixture
async def app(tmp_path: Any) -> FastAPI:
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openai-main",
                catalog_id="openai",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://openai.internal-example.test/v1",
                api_key="sk-openai-super-secret-value",
                models=["gpt-4o"],
            )
        ],
    )
    application = with_dashboard_auth(create_app(config=config))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        await client.get("/dashboard/api/v2/state/overview", headers=HEADERS)
    db_path = application.state.db_path
    now_sql = (
        datetime.datetime.now(datetime.UTC).replace(microsecond=0, tzinfo=None).isoformat(sep=" ")
    )
    await seed_usage(
        db_path,
        [
            {
                "timestamp": now_sql,
                "provider_id": "openai-main",
                "model": "deepseek-chat",
                "input_tokens": 1_000_000,
                "output_tokens": 500_000,
                "cost": 2.0,
            },
            {
                "timestamp": now_sql,
                "provider_id": "openai-main",
                "model": "freebie",
                "input_tokens": 1_000,
                "output_tokens": 0,
                "cost": 0.0,
            },
        ],
    )
    return application


async def _get(app: FastAPI, path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=HEADERS)


async def test_analytics_section_includes_savings(app: FastAPI) -> None:
    response = await _get(app, "/dashboard/api/v2/state/analytics")
    assert response.status_code == 200
    savings = response.json()["data"]["savings"]
    assert savings["baseline"] == "gpt-4o"
    assert savings["baseline_priced"] is True
    assert savings["requests"] == 1
    assert savings["actual_cost"] == pytest.approx(2.0)
    assert savings["excluded"] == {"subscription_requests": 0, "unpriced_requests": 1}
    assert savings["window"] == {"kind": "days", "days": 30}
    assert savings["savings"] > 0
    row = savings["by_model"][0]
    assert row["model"] == "deepseek-chat"
    assert row["actual_cost"] == pytest.approx(2.0)


async def test_analytics_baseline_query_param(app: FastAPI) -> None:
    cheap = await _get(app, "/dashboard/api/v2/state/analytics?baseline=gpt-4o-mini")
    assert cheap.status_code == 200
    baseline = cheap.json()["data"]["savings"]["baseline"]
    assert baseline == "gpt-4o-mini"
    meta = cheap.json()["meta"]["query"]
    assert meta["baseline"] == "gpt-4o-mini"

    unknown = await _get(app, "/dashboard/api/v2/state/analytics?baseline=no-such-model")
    assert unknown.status_code == 422
    assert "baseline" in unknown.json()["detail"]


async def test_overview_includes_savings_today(app: FastAPI) -> None:
    response = await _get(app, "/dashboard/api/v2/state/overview")
    assert response.status_code == 200
    savings = response.json()["data"]["savings_today"]
    assert savings["window"]["kind"] == "today"
    assert "reporting_timezone" in savings["window"]
    assert "by_model" not in savings
    assert savings["requests"] == 1
    assert savings["actual_cost"] == pytest.approx(2.0)


async def test_public_savings_endpoint(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anonymous = await client.get("/v1/analytics/savings")
        assert anonymous.status_code == 401

        full_key, _meta = await create_key(app.state.db_path, "savings-test")
        authed = await client.get(
            "/v1/analytics/savings",
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert authed.status_code == 200
        payload = authed.json()
        assert payload["baseline"] == "gpt-4o"
        assert payload["requests"] == 1
        assert payload["savings"] > 0

        bad = await client.get(
            "/v1/analytics/savings?baseline=no-such-model",
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert bad.status_code == 422

        clamped = await client.get(
            "/v1/analytics/savings?days=0",
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert clamped.status_code == 422


async def test_savings_baseline_setting_roundtrip(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.post(
            "/dashboard/api/settings",
            data={"key": "analytics_savings_baseline", "value": "gpt-4o-mini"},
            headers=HEADERS,
        )
        assert updated.status_code == 200, updated.text

        invalid = await client.post(
            "/dashboard/api/settings",
            data={"key": "analytics_savings_baseline", "value": "  "},
            headers=HEADERS,
        )
        assert invalid.status_code == 400

    after = await _get(app, "/dashboard/api/v2/state/analytics")
    assert after.json()["data"]["savings"]["baseline"] == "gpt-4o-mini"
