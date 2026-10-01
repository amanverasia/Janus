from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting

pytestmark = pytest.mark.asyncio

CHEAP_UPSTREAM = "https://cheap.local/v1"
PRICEY_UPSTREAM = "https://pricey.local/v1"
BODY = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


def _openai_completion(model: str) -> dict[str, Any]:
    return {
        "id": "r1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


async def _seed_and_reload(app: FastAPI) -> None:
    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.storage.database import init_db, seed_from_config

    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)


@pytest.fixture
async def app(tmp_path: Any) -> FastAPI:
    config = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="cheap",
                prefix="deepseek",
                api_type="openai_compat",
                base_url=CHEAP_UPSTREAM,
                api_key="sk-cheap",
                models=["deepseek-chat"],
                allowed_models=["deepseek-chat"],
            ),
            ProviderConfig(
                id="pricey",
                prefix="openai",
                api_type="openai_compat",
                base_url=PRICEY_UPSTREAM,
                api_key="sk-pricey",
                models=["gpt-4o-mini"],
                allowed_models=["gpt-4o-mini"],
            ),
        ],
    )
    application = create_app(config=config)
    await _seed_and_reload(application)
    await set_setting(application.state.db_path, "auto_routing_strategy", "cheapest")
    return application


async def _post(app: FastAPI, body: dict[str, Any] | None = None) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/chat/completions", json=body or BODY)


@respx.mock
async def test_auto_routes_to_cheapest_model(app: FastAPI) -> None:
    respx.post(f"{CHEAP_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("deepseek-chat")
    )
    pricey = respx.post(f"{PRICEY_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("gpt-4o-mini")
    )
    response = await _post(app)
    assert response.status_code == 200
    assert pricey.called
    assert response.headers["x-janus-requested-model"] == "auto"
    assert response.headers["x-janus-resolved-model"] == "gpt-4o-mini"


@respx.mock
async def test_auto_fallback_cascades_to_next_model(app: FastAPI) -> None:
    respx.post(f"{PRICEY_UPSTREAM}/chat/completions").respond(500, json={"error": "boom"})
    cheap = respx.post(f"{CHEAP_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("deepseek-chat")
    )
    response = await _post(app)
    assert response.status_code == 200
    assert cheap.called
    assert response.headers["x-janus-resolved-model"] == "deepseek-chat"
    assert response.headers["x-janus-fallback"] == "true"


@respx.mock
async def test_auto_respects_key_allowlist(app: FastAPI) -> None:
    from janus.storage.api_keys import create_key

    full_key, _meta = await create_key(
        app.state.db_path, "auto-limited", allowed_models=["deepseek/deepseek-chat"]
    )
    cheap = respx.post(f"{CHEAP_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("deepseek-chat")
    )
    pricey = respx.post(f"{PRICEY_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("gpt-4o-mini")
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {full_key}"}
        )
    assert response.status_code == 200
    assert cheap.called
    assert not pricey.called
    assert response.headers["x-janus-resolved-model"] == "deepseek-chat"


@respx.mock
async def test_auto_no_candidates_returns_503(app: FastAPI) -> None:
    from janus.storage.api_keys import create_key

    full_key, _meta = await create_key(
        app.state.db_path, "auto-none", allowed_models=["openai/gpt-4o-mini"]
    )
    handler = app.state.provider_snapshot.handler
    handler.mark_cooldown("pricey", "rate_limit")
    respx.post(f"{PRICEY_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("gpt-4o-mini")
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {full_key}"}
        )
    assert response.status_code == 503


@respx.mock
async def test_auto_request_log_records_resolved_model(app: FastAPI) -> None:
    await set_setting(app.state.db_path, "server_request_logging", "true")
    respx.post(f"{PRICEY_UPSTREAM}/chat/completions").respond(
        200, json=_openai_completion("gpt-4o-mini")
    )
    response = await _post(app)
    assert response.status_code == 200
    logs = await list_request_logs(app.state.db_path, limit=5)
    assert logs, "request log not written"
    row = logs[0]
    assert row["model"] == "auto"
    assert row["resolved_model"] == "gpt-4o-mini"


async def test_auto_strategy_setting_validation(app: FastAPI) -> None:
    from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

    with_dashboard_auth(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}"}
        ok = await client.post(
            "/dashboard/api/settings",
            data={"key": "auto_routing_strategy", "value": "fastest"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        bad = await client.post(
            "/dashboard/api/settings",
            data={"key": "auto_routing_strategy", "value": "ludicrous"},
            headers=headers,
        )
        assert bad.status_code == 400
