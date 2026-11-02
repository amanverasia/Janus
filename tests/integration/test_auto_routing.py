from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.routing.auto import AutoStrategy, plan_auto
from janus.storage.database import get_connection
from janus.storage.model_signals import get_model_signals, invalidate_model_signals_cache
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

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


@pytest.fixture
async def same_model_app(tmp_path: Any) -> AsyncIterator[FastAPI]:
    from janus.api.signals import drain_attempt_signal_tasks
    from janus.routing.provider_snapshots import close_provider_snapshots

    config = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id=prefix,
                prefix=prefix,
                api_type="openai_compat",
                base_url=f"https://{prefix}.local/v1",
                api_key=f"sk-{prefix}",
                models=["gpt-4o"],
                allowed_models=["gpt-4o"],
            )
            for prefix in ["alpha", "beta"]
        ],
    )
    application = with_dashboard_auth(create_app(config=config))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        initialized = await client.get("/dashboard/api/v2/state/routing")
        assert initialized.status_code == 200
    await set_setting(application.state.db_path, "auto_routing_strategy", "cheapest")
    yield application
    await drain_attempt_signal_tasks()
    await application.state.fallback_handler._drain_persist_tasks()
    await close_provider_snapshots(application)


@pytest.mark.parametrize("cooled_primary", [False, True])
@respx.mock
async def test_auto_same_model_provider_fallback_matches_preview(
    same_model_app: FastAPI, cooled_primary: bool
) -> None:
    app = same_model_app
    alpha = respx.post("https://alpha.local/v1/chat/completions").respond(
        500, json={"error": "unavailable"}
    )
    beta = respx.post("https://beta.local/v1/chat/completions").respond(
        200, json=_openai_completion("gpt-4o")
    )
    if cooled_primary:
        app.state.fallback_handler.mark_cooldown("alpha", "auth", model="gpt-4o")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = await client.get(
            "/v1/quality/auto-preview",
            headers={"Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}"},
        )
        routing = await client.get("/dashboard/api/v2/state/routing")
    assert preview.status_code == routing.status_code == 200
    expected = ["alpha/gpt-4o", "beta/gpt-4o"]
    assert preview.json()["models"] == routing.json()["data"]["auto"]["models"] == expected
    assert [entry["model"] for entry in preview.json()["trace"]] == expected
    assert preview.json()["excluded"] == []

    response = await _post(app)

    assert response.status_code == 200
    assert beta.call_count == 1
    assert alpha.called is not cooled_primary
    assert response.headers["x-janus-resolved-model"] == "gpt-4o"


@pytest.mark.parametrize(
    ("allowed", "expected_status"),
    [(["alpha/*"], 503), (["beta/gpt-4o"], 200)],
)
@respx.mock
async def test_auto_same_model_alternate_respects_key_allowlist(
    same_model_app: FastAPI, allowed: list[str], expected_status: int
) -> None:
    from janus.storage.api_keys import create_key

    app = same_model_app
    full_key, _meta = await create_key(app.state.db_path, "limited", allowed_models=allowed)
    app.state.fallback_handler.mark_cooldown("alpha", "auth", model="gpt-4o")
    alpha = respx.post("https://alpha.local/v1/chat/completions").respond(
        200, json=_openai_completion("gpt-4o")
    )
    beta = respx.post("https://beta.local/v1/chat/completions").respond(
        200, json=_openai_completion("gpt-4o")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {full_key}"}
        )

    assert response.status_code == expected_status
    assert not alpha.called
    assert beta.called is (expected_status == 200)


@respx.mock
async def test_auto_same_model_alternates_and_signals_refresh_on_reload(
    same_model_app: FastAPI,
) -> None:
    from janus.storage.providers_db import toggle_provider

    app = same_model_app
    db_path = app.state.db_path
    old_snapshot = app.state.provider_snapshot
    old_snapshot.handler.mark_cooldown("alpha", "auth", model="gpt-4o")
    await old_snapshot.handler._drain_persist_tasks()
    assert (await get_model_signals(db_path)).by_model == {}
    async with get_connection(db_path) as db:
        await db.executemany(
            "INSERT INTO attempt_signals (model, account_id, streamed, outcome,"
            " ttft_ms, duration_ms, output_tokens, output_tps)"
            " VALUES ('gpt-4o', ?, 1, ?, 300, 900, 100, 100.0)",
            [("alpha", "error")] * 5 + [("beta", "ok")] * 5,
        )
        await db.commit()
    await set_setting(db_path, "auto_routing_strategy", "quality")
    await reload_providers(app)

    snapshot = app.state.provider_snapshot
    assert snapshot is not old_snapshot
    assert not snapshot.handler.is_available("alpha", "gpt-4o")
    signals = await get_model_signals(db_path)
    assert signals.by_model["gpt-4o"].error_rate == 0.5
    assert signals.by_account[("gpt-4o", "alpha")].error_rate == 1.0
    assert signals.by_account[("gpt-4o", "beta")].error_rate == 0.0
    plan = await plan_auto(
        registry=snapshot.registry,
        db_path=db_path,
        pricing_registry=app.state.pricing_registry,
        strategy=AutoStrategy.QUALITY,
        top_n=1,
    )
    assert plan.models == ["alpha/gpt-4o", "beta/gpt-4o"]

    alpha = respx.post("https://alpha.local/v1/chat/completions").respond(
        200, json=_openai_completion("gpt-4o")
    )
    beta = respx.post("https://beta.local/v1/chat/completions").respond(
        200, json=_openai_completion("gpt-4o")
    )
    assert (await _post(app)).status_code == 200
    assert not alpha.called
    assert beta.call_count == 1

    await toggle_provider(db_path, "beta")
    await reload_providers(app)
    assert (await _post(app)).status_code == 503
    assert beta.call_count == 1

    await toggle_provider(db_path, "beta")
    await reload_providers(app)
    assert (await _post(app)).status_code == 200
    assert not alpha.called
    assert beta.call_count == 2


@respx.mock
async def test_auto_preview_attributes_signals_per_provider(same_model_app: FastAPI) -> None:
    from janus.routing.auto import serialize_chain

    app = same_model_app
    db_path = app.state.db_path
    async with get_connection(db_path) as db:
        await db.executemany(
            "INSERT INTO attempt_signals (model, provider_id, account_id, streamed, outcome,"
            " ttft_ms, duration_ms, output_tokens, output_tps)"
            " VALUES ('gpt-4o', ?, ?, 1, ?, ?, 900, 100, ?)",
            [("alpha", "alpha", "error", 300, 100.0)] * 6 + [("beta", "beta", "ok", 250, 80.0)] * 6,
        )
        await db.commit()
    invalidate_model_signals_cache(db_path)
    plan = await plan_auto(
        registry=app.state.provider_snapshot.registry,
        db_path=db_path,
        pricing_registry=app.state.pricing_registry,
        strategy=AutoStrategy.QUALITY,
    )
    rows = {row["model"]: row for row in serialize_chain(plan)}
    assert [row["model"] for row in serialize_chain(plan)] == plan.models
    assert rows["alpha/gpt-4o"]["error_rate"] == 1.0
    assert rows["beta/gpt-4o"]["error_rate"] == 0.0
    assert rows["alpha/gpt-4o"]["signal_scope"] == "provider"
    assert rows["beta/gpt-4o"]["samples"] == 6
