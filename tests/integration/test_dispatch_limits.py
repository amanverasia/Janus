import asyncio
import json
import time

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.routing.provider_snapshots import close_provider_snapshots
from janus.storage.outcomes import list_request_outcomes
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting
from tests.integration.test_api import _seed_and_reload

BODY = {"model": "pool/m", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
async def app(tmp_path):
    config = ProviderConfig(
        id="account-0",
        prefix="pool",
        api_type="openai_compat",
        base_url="https://upstream.test/v1",
        models=["m"],
    )
    app = create_app(
        config=JanusConfig(
            server=ServerSettings(require_api_key=False, data_dir=tmp_path), providers=[config]
        )
    )
    await _seed_and_reload(app)
    await set_setting(app.state.db_path, "server_request_logging", "true")
    await set_setting(app.state.db_path, "server_dispatch_timeout_s", "1")
    yield app
    await _drain_stream_persist_tasks()
    await app.state.fallback_handler._drain_persist_tasks()
    await close_provider_snapshots(app)


async def post(app, body=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/chat/completions", json=body or BODY)


@respx.mock
async def test_hundreds_of_accounts_stop_at_attempt_limit_with_one_outcome(app):
    snapshot = app.state.provider_snapshot
    original = snapshot.registry.lookup("pool/m")[0].provider_config
    for i in range(1, 200):
        config = original.model_copy(update={"id": f"account-{i}"})
        snapshot.registry.register(config)
        snapshot.providers[config.id] = snapshot.providers[original.id]
    snapshot.handler.cooldowns_enabled = False
    await set_setting(app.state.db_path, "server_dispatch_max_attempts", "3")
    upstream = respx.post("https://upstream.test/v1/chat/completions").respond(
        429, json={"error": "rate limited"}
    )
    response = await post(app)
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "dispatch_attempt_limit"
    assert response.json()["error"]["attempted"] == 3
    assert response.json()["error"]["skipped"] == 197
    assert upstream.call_count == 3
    rows = await list_request_outcomes(app.state.db_path)
    assert len(rows) == 1
    assert rows[0]["attempts"] == 3
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert "dispatch_attempt_limit" in logs[0]["error"]


@pytest.mark.parametrize("during_preprocessing", [False, True])
async def test_deadline_cancels_work_and_records_actual_attempt_count(
    app, monkeypatch, during_preprocessing
):
    closed = asyncio.Event()

    async def stall(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    if during_preprocessing:
        monkeypatch.setattr(type(app.state.saver_pipeline), "apply_async_traced", stall)
    else:
        monkeypatch.setattr(app.state.providers["account-0"], "call", stall)
    start = time.monotonic()
    response = await post(app)
    assert 0.8 < time.monotonic() - start < 2.5
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "dispatch_deadline_exceeded"
    assert response.json()["error"]["attempted"] == (0 if during_preprocessing else 1)
    assert closed.is_set()
    rows = await list_request_outcomes(app.state.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == 504
    assert rows[0]["attempts"] == (0 if during_preprocessing else 1)


class StalledStream(httpx.AsyncByteStream):
    def __init__(self, *, emit_first=False):
        self.emit_first = emit_first
        self.closed = False

    async def __aiter__(self):
        if self.emit_first:
            data = {"choices": [{"index": 0, "delta": {"content": "hello"}}]}
            yield f"data: {json.dumps(data)}\n\n".encode()
            await asyncio.sleep(1.1)
            yield b"data: [DONE]\n\n"
        else:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("emit_first", [False, True])
@respx.mock
async def test_stream_deadline_ends_after_first_client_output(app, emit_first):
    stream = StalledStream(emit_first=emit_first)
    upstream = respx.post("https://upstream.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, stream=stream)
    )
    response = await post(app, {**BODY, "stream": True})
    assert response.status_code == (200 if emit_first else 504)
    assert upstream.call_count == 1
    assert stream.closed
    await _drain_stream_persist_tasks()
    rows = await list_request_outcomes(app.state.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == (200 if emit_first else 504)
    assert rows[0]["attempts"] == 1


@respx.mock
async def test_disconnect_between_attempts_prevents_more_dispatch(app, monkeypatch):
    disconnected = False

    async def is_disconnected(self):
        return disconnected

    def refuse(request):
        nonlocal disconnected
        disconnected = True
        return httpx.Response(429, json={"error": "rate limited"})

    monkeypatch.setattr("starlette.requests.Request.is_disconnected", is_disconnected)
    original = app.state.registry.lookup("pool/m")[0].provider_config
    alternate = original.model_copy(update={"id": "alternate"})
    app.state.registry.register(alternate)
    app.state.providers["alternate"] = app.state.providers[original.id]
    upstream = respx.post("https://upstream.test/v1/chat/completions").mock(side_effect=refuse)
    response = await post(app)
    assert response.status_code == 499
    assert upstream.call_count == 1
    rows = await list_request_outcomes(app.state.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == 499
    assert rows[0]["attempts"] == 1
