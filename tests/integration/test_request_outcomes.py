"""Issue #103: one terminal outcome row per client request, on every path.

Analytics success rate and request totals must match what clients actually
experienced: parse errors, budget blocks, exhausted fallback, non-fallback
upstream errors, interrupted streams, and successes all count — with request
logging ON and OFF.
"""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.settings import set_setting

GOOD_BODY = {
    "id": "r1",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
}

GOOD_SSE = (
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


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
async def one_account_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="acct-a",
                prefix="test",
                api_type="openai_compat",
                base_url="https://a.local/v1",
                api_key="sk-a",
                models=["m1"],
            ),
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture
async def two_account_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="acct-a",
                prefix="test",
                api_type="openai_compat",
                base_url="https://a.local/v1",
                api_key="sk-a",
                models=["m1"],
            ),
            ProviderConfig(
                id="acct-b",
                prefix="test",
                api_type="openai_compat",
                base_url="https://b.local/v1",
                api_key="sk-b",
                models=["m1"],
            ),
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


async def _outcomes(app) -> list[dict]:
    from janus.storage.outcomes import list_request_outcomes

    return await list_request_outcomes(app.state.db_path, limit=50)


async def _set_logging(app, enabled: bool) -> None:
    await set_setting(app.state.db_path, "server_request_logging", "1" if enabled else "0")


def _post_body(**overrides) -> dict:
    body = {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}
    body.update(overrides)
    return body


# ── logging ON vs OFF ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_success_outcome_recorded_logging_on(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 200
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 200
    assert rows[0]["model"] == "m1"
    assert rows[0]["attempts"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_success_outcome_recorded_logging_off(one_account_app):
    await _set_logging(one_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 200
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 200


# ── parse/client errors ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_body_outcome_recorded_logging_off(one_account_app):
    await _set_logging(one_account_app, False)
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post(
            "http://test/v1/chat/completions",
            json={"model": "m1", "messages": ["not-a-dict"]},
        )
    assert r.status_code == 400
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 400
    assert rows[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_unknown_model_outcome_recorded_logging_off(one_account_app):
    await _set_logging(one_account_app, False)
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body(model="nope"))
    assert r.status_code == 400
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 400
    assert rows[0]["model"] == "nope"


# ── budget blocks ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_budget_block_outcome_recorded_logging_off(one_account_app):
    from janus.storage.budgets import create_or_update_budget

    await _set_logging(one_account_app, False)
    await create_or_update_budget(one_account_app.state.db_path, key_id=None, daily_limit=0.0)
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 429
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 429
    assert rows[0]["model"] == "m1"


# ── exhausted fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_fallback_records_single_503(two_account_app):
    await _set_logging(two_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 503
    rows = await _outcomes(two_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 503
    assert rows[0]["attempts"] == 2


# ── early failed account followed by success ──────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_early_failure_then_success_records_one_200(two_account_app):
    await _set_logging(two_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 200
    rows = await _outcomes(two_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 200
    assert rows[0]["attempts"] == 2


# ── non-fallback upstream error ────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_non_fallback_upstream_400_recorded(one_account_app):
    await _set_logging(one_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as client:
        r = await client.post("http://test/v1/chat/completions", json=_post_body())
    assert r.status_code == 400
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 400
    assert rows[0]["provider_id"] == "acct-a"


# ── streaming: success and interruption ────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_streaming_success_outcome_recorded(one_account_app):
    await _set_logging(one_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=GOOD_SSE.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    transport = ASGITransport(app=one_account_app)
    async with AsyncClient(transport=transport) as client:
        async with client.stream(
            "POST", "http://test/v1/chat/completions", json=_post_body(stream=True)
        ) as response:
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
    assert b"DONE" in body
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 200
    assert rows[0]["streamed"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_streaming_interruption_records_502(one_account_app):
    await _set_logging(one_account_app, False)
    sse = GOOD_SSE.encode()
    half = len(sse) // 2

    async def _broken_stream():
        yield sse[:half]
        raise RuntimeError("connection reset mid-stream")

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=_broken_stream(),
            headers={"content-type": "text/event-stream"},
        )
    )
    transport = ASGITransport(app=one_account_app)
    async with AsyncClient(transport=transport) as client:
        async with client.stream(
            "POST", "http://test/v1/chat/completions", json=_post_body(stream=True)
        ) as response:
            assert response.status_code == 200
            async for _ in response.aiter_bytes():
                pass
    rows = await _outcomes(one_account_app)
    assert len(rows) == 1
    assert rows[0]["status"] == 502
    assert rows[0]["streamed"] == 1


# ── analytics agreement ────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_analytics_success_rate_matches_terminal_outcomes(two_account_app):
    from janus.storage.analytics import get_success_rate

    await _set_logging(two_account_app, False)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as client:
        for _ in range(2):
            r = await client.post("http://test/v1/chat/completions", json=_post_body())
            assert r.status_code == 200
        r = await client.post("http://test/v1/chat/completions", json=_post_body(model="nope"))
        assert r.status_code == 400

    rows = await _outcomes(two_account_app)
    assert len(rows) == 3
    success = await get_success_rate(two_account_app.state.db_path, days=1)
    assert success["total"] == 3
    assert success["success_2xx"] == 2
    assert success["client_4xx"] == 1
