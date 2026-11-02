import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.attempt_signals import list_attempt_signals
from janus.storage.outcomes import list_request_outcomes
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting
from tests.fixtures.dashboard_auth import with_dashboard_auth

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


def _provider(pid: str, host: str) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        prefix="test",
        api_type="openai_compat",
        base_url=f"https://{host}/v1",
        api_key=f"sk-{pid}",
        models=["m1"],
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
async def app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            _provider("acct-a", "a.local"),
            _provider("acct-b", "b.local"),
            _provider("acct-c", "c.local"),
        ],
    )
    app = with_dashboard_auth(create_app(config=cfg))
    await _seed_and_reload(app)
    return app


def _body():
    return {"model": "m1", "messages": [{"role": "user", "content": "synthetic"}]}


def _mock_chain():
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(401))
    respx.post("https://b.local/v1/chat/completions").mock(return_value=httpx.Response(402))
    respx.post("https://c.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )


@respx.mock
async def test_401_402_success_shares_one_request_id_and_three_ordered_attempts(app):
    _mock_chain()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=_body())
        assert response.status_code == 200
        await _drain_stream_persist_tasks()
        log = (await list_request_logs(app.state.db_path))[0]
        detail_response = await client.get(
            f"/dashboard/api/request-logs/{log['id']}", params={"attempt_limit": 2}
        )

    await _drain_stream_persist_tasks()
    outcome = (await list_request_outcomes(app.state.db_path))[0]
    signals = sorted(
        await list_attempt_signals(app.state.db_path), key=lambda row: row["attempt_order"]
    )
    request_id = log["request_id"]
    assert request_id
    assert outcome["request_id"] == request_id
    assert {row["request_id"] for row in signals} == {request_id}
    assert [row["attempt_order"] for row in signals] == [1, 2, 3]
    assert [row["status"] for row in signals] == [401, 402, 200]
    assert [row["failure_class"] for row in signals] == ["auth_error", "payment_error", None]
    assert [row["cooldown_decision"] for row in signals] == ["applied", "applied", None]
    assert outcome["attempts"] == 3

    assert detail_response.status_code == 200
    assert detail_response.headers["cache-control"] == "no-store"
    detail = detail_response.json()
    assert "request_body" not in detail
    assert "response_body" not in detail
    assert detail["has_bodies"] is True
    assert detail["request_id"] == request_id
    assert detail["outcome"]["status"] == 200
    assert detail["outcome"]["attempts"] == 3
    assert detail["attempt_pagination"]["total"] == 3
    assert [row["status"] for row in detail["attempts"]] == [401, 402]
    assert all("account_id" not in row for row in detail["attempts"])
    serialized_detail = json.dumps(detail)
    assert all(account not in serialized_detail for account in ("acct-a", "acct-b", "acct-c"))
    assert detail["attempts"][0]["recovery"]["label"] == "Review credentials"
    assert detail["attempts"][1]["recovery"]["label"] == "Review billing and quota"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second_page = await client.get(
            f"/dashboard/api/request-logs/{log['id']}",
            params={"attempt_limit": 2, "attempt_offset": 2},
        )
        assert [row["status"] for row in second_page.json()["attempts"]] == [200]
        revealed = await client.get(
            f"/dashboard/api/request-logs/{log['id']}", params={"include_bodies": "true"}
        )
        assert "synthetic" in revealed.json()["request_body"]
        assert "OK" in revealed.json()["response_body"]


@respx.mock
async def test_correlation_survives_concurrent_identical_requests(app):
    _mock_chain()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            client.post("/v1/chat/completions", json=_body()),
            client.post("/v1/chat/completions", json=_body()),
        )
        assert all(response.status_code == 200 for response in responses)
        await _drain_stream_persist_tasks()
        logs = await list_request_logs(app.state.db_path)
        assert len(logs) == 2
        details = await asyncio.gather(
            *(client.get(f"/dashboard/api/request-logs/{log['id']}") for log in logs)
        )

    outcomes = await list_request_outcomes(app.state.db_path)
    signals = await list_attempt_signals(app.state.db_path)
    request_ids = {log["request_id"] for log in logs}
    assert len(request_ids) == 2
    assert {row["request_id"] for row in outcomes} == request_ids
    assert {row["request_id"] for row in signals} == request_ids
    for detail_response in details:
        detail = detail_response.json()
        expected = detail["outcome"]["attempts"]
        assert detail["attempt_pagination"]["total"] == expected
        assert len(detail["attempts"]) == expected
        assert [row["attempt_order"] for row in detail["attempts"]] == list(range(1, expected + 1))


@respx.mock
async def test_outcomes_and_attempts_correlate_with_request_logging_disabled(app):
    _mock_chain()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=_body())
        assert response.status_code == 200
    await _drain_stream_persist_tasks()
    assert await list_request_logs(app.state.db_path) == []
    outcome = (await list_request_outcomes(app.state.db_path))[0]
    signals = await list_attempt_signals(app.state.db_path)
    assert outcome["request_id"]
    assert len(signals) == 3
    assert {row["request_id"] for row in signals} == {outcome["request_id"]}


@respx.mock
async def test_refined_quota_failure_keeps_rate_limit_classification(app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "quota exceeded"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=_body())
        assert response.status_code == 200
        await _drain_stream_persist_tasks()
        log = (await list_request_logs(app.state.db_path))[0]
        detail = (await client.get(f"/dashboard/api/request-logs/{log['id']}")).json()
    assert detail["attempts"][0]["status"] == 400
    assert detail["attempts"][0]["failure_class"] == "rate_limit"
    assert detail["attempts"][0]["cooldown"]["decision"] == "applied"
    assert detail["attempts"][0]["recovery"]["label"] == "Review account limits"


@respx.mock
async def test_stream_network_failure_records_cooldown_metadata(app):
    async def broken_stream() -> AsyncIterator[bytes]:
        yield (
            b'data: {"id":"r1","object":"chat.completion.chunk",'
            b'"choices":[{"index":0,"delta":{"role":"assistant"},'
            b'"finish_reason":null}]}\n\n'
        )
        raise httpx.ReadError("synthetic stream failure")

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=broken_stream(), headers={"content-type": "text/event-stream"}
        )
    )
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json={**_body(), "stream": True})
        assert response.status_code == 200
        await _drain_stream_persist_tasks()
        log = (await list_request_logs(app.state.db_path))[0]
        detail = (await client.get(f"/dashboard/api/request-logs/{log['id']}")).json()
    assert detail["attempts"][0]["outcome"] == "error"
    assert detail["attempts"][0]["failure_class"] == "network"
    assert detail["attempts"][0]["cooldown"]["decision"] == "applied"
    assert detail["attempts"][0]["cooldown"]["seconds"] is not None


@respx.mock
async def test_void_canonical_stream_records_server_failure_and_cooldown(app):
    void_sse = "data: [DONE]\n\n"
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=void_sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    await set_setting(app.state.db_path, "server_request_logging", "true")
    payload = {
        "model": "m1",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "synthetic"}],
        "stream": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/messages", json=payload)
        assert response.status_code == 200
        await _drain_stream_persist_tasks()
        log = (await list_request_logs(app.state.db_path))[0]
        detail = (await client.get(f"/dashboard/api/request-logs/{log['id']}")).json()
    assert detail["attempts"][0]["status"] == 200
    assert detail["attempts"][0]["outcome"] == "error"
    assert detail["final_outcome"] == "upstream_failure"
    assert detail["attempts"][0]["failure_class"] == "server_error"
    assert detail["attempts"][0]["cooldown"]["decision"] == "applied"
    assert detail["attempts"][0]["cooldown"]["seconds"] is not None
