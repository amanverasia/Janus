"""Probed window headroom reorders try-order and surfaces in the attempt trail."""

import time

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting


async def _seed_and_reload(app) -> None:
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
async def app(tmp_path):
    """Spent account (acct1) registered first; probe data should demote it."""
    accounts = [
        ProviderConfig(
            id="acct1::uk_spent",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="sk-spent",
            models=["m1"],
        ),
        ProviderConfig(
            id="acct2::uk_fresh",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key="sk-fresh",
            models=["m1"],
        ),
    ]
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=accounts,
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _seed_probed_usage(app, *, spent_account: str, percent: float) -> None:
    handler = app.state.provider_snapshot.handler
    handler._probed_used = {spent_account: (percent, time.time())}


def _ok_json() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "r1",
            "object": "chat.completion",
            "model": "m1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        },
    )


@pytest.mark.asyncio
@respx.mock
async def test_spent_window_key_tried_after_fresh_key(app):
    _seed_probed_usage(app, spent_account="acct1::uk_spent", percent=95.0)
    calls: list[str] = []

    def route() -> respx.Route:
        return respx.post("https://fake.local/v1/chat/completions").mock(
            side_effect=lambda request: (
                calls.append(request.headers.get("authorization", "")),
                _ok_json(),
            )[1]
        )

    route()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert calls, "upstream must have been called"
    assert calls[0] == "Bearer sk-fresh"


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_probe_data_never_blocks_requests(app):
    _seed_probed_usage(app, spent_account="acct1::uk_spent", percent=100.0)
    respx.post("https://fake.local/v1/chat/completions").mock(return_value=_ok_json())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_attempt_trail_records_probed_reorder(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    _seed_probed_usage(app, spent_account="acct1::uk_spent", percent=95.0)
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "attempt 2: 429 (probed window 95% used)" in detail
    assert "acct1" not in detail
    assert "uk_spent" not in detail

    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    trail = logs[0]["error"]
    assert trail.index("acct2::uk_fresh") < trail.index("acct1::uk_spent")
    assert "acct1::uk_spent: 429 (probed window 95% used)" in trail


@pytest.mark.asyncio
@respx.mock
async def test_stale_probe_data_leaves_try_order_alone(app):
    handler = app.state.provider_snapshot.handler
    handler._probed_used = {"acct1::uk_spent": (95.0, time.time() - 700.0)}
    calls: list[str] = []

    respx.post("https://fake.local/v1/chat/completions").mock(
        side_effect=lambda request: (
            calls.append(request.headers.get("authorization", "")),
            _ok_json(),
        )[1]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert calls[0] == "Bearer sk-spent"
