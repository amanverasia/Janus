from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

pytestmark = pytest.mark.asyncio

HEADERS = {"Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}", "Accept": "application/json"}


@pytest.fixture
async def app(tmp_path: Any) -> FastAPI:
    config = JanusConfig(
        server=ServerSettings(port=0, require_api_key=True, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="cheap",
                prefix="deepseek",
                api_type="openai_compat",
                base_url="https://cheap.local/v1",
                api_key="sk-cheap",
                models=["deepseek-chat"],
                allowed_models=["deepseek-chat"],
            ),
            ProviderConfig(
                id="pricey",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://pricey.local/v1",
                api_key="sk-pricey",
                models=["gpt-4o-mini"],
                allowed_models=["gpt-4o-mini"],
            ),
        ],
    )
    application = with_dashboard_auth(create_app(config=config))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        await client.get("/dashboard/api/v2/state/overview", headers=HEADERS)
    return application


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_override_crud_endpoints(app: FastAPI) -> None:
    async with _client(app) as client:
        created = await client.post(
            "/dashboard/api/v2/auto-overrides",
            json={"model": "deepseek-chat", "quality": 0.95, "note": "operator pick"},
            headers=HEADERS,
        )
        assert created.status_code == 200, created.text
        assert created.json()["override"]["model"] == "deepseek-chat"

        invalid = await client.post(
            "/dashboard/api/v2/auto-overrides",
            json={"model": "deepseek-chat", "quality": 3.0},
            headers=HEADERS,
        )
        assert invalid.status_code == 400

        listed = await client.get("/dashboard/api/v2/state/routing?limit=1", headers=HEADERS)
        assert listed.status_code == 200
        overrides = listed.json()["data"]["auto"]["overrides"]
        assert [entry["model"] for entry in overrides] == ["deepseek-chat"]

        removed = await client.delete(
            "/dashboard/api/v2/auto-overrides/deepseek-chat", headers=HEADERS
        )
        assert removed.status_code == 200
        missing = await client.delete(
            "/dashboard/api/v2/auto-overrides/deepseek-chat", headers=HEADERS
        )
        assert missing.status_code == 404


async def test_override_wins_in_preview_and_routing(app: FastAPI) -> None:
    from janus.storage.api_keys import create_key
    from janus.storage.settings import set_setting

    await set_setting(app.state.db_path, "auto_routing_strategy", "quality")
    async with _client(app) as client:
        await client.post(
            "/dashboard/api/v2/auto-overrides",
            json={"model": "deepseek-chat", "quality": 1.0},
            headers=HEADERS,
        )

    full_key, _meta = await create_key(app.state.db_path, "preview-user")
    async with _client(app) as client:
        preview = await client.get(
            "/v1/quality/auto-preview",
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["strategy"] == "quality"
    assert payload["models"][0] == "deepseek/deepseek-chat"
    top = payload["trace"][0]
    assert top["model"] == "deepseek/deepseek-chat"
    assert top["overridden"] is True


async def test_auto_preview_auth_and_validation(app: FastAPI) -> None:
    from janus.storage.api_keys import create_key

    full_key, _meta = await create_key(app.state.db_path, "preview-auth")
    async with _client(app) as client:
        anonymous = await client.get("/v1/quality/auto-preview")
        assert anonymous.status_code == 401

        ok = await client.get(
            "/v1/quality/auto-preview?strategy=cheapest",
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert ok.status_code == 200
        assert ok.json()["strategy"] == "cheapest"
        assert {"model": "openai/gpt-4o-mini", "reason": "unpriced"} not in [
            {"model": entry["model"], "reason": entry["reason"]} for entry in ok.json()["excluded"]
        ]

        bad = await client.get(
            "/v1/quality/auto-preview?strategy=nope",
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert bad.status_code == 422
