"""Issue #200: aborted client streams must still persist usage/outcome/log rows.

A client disconnect mid-stream cancels the streaming task group, so bookkeeping
awaited in the generator's ``finally`` used to die with the first suspending
await. Client aborts are recorded with status 499 (nginx "client closed
request"), never as upstream 502s.
"""

import asyncio
import gc
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import aiosqlite
import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.outcomes import list_request_outcomes
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting

ROLE_LINE = (
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
)
CONTENT_LINE = (
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
)

REQUEST_BODY = {
    "model": "test/m1",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
}


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


async def _usage_rows(db_path) -> list[dict[str, Any]]:
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider_id, model, account_id, status, input_tokens, output_tokens, cost"
            " FROM usage"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def _abort_telemetry_rows(
    db_path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        await _usage_rows(db_path),
        await list_request_outcomes(db_path),
        await list_request_logs(db_path),
    )


def _assert_abort_rows(
    usage_rows: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    logs: list[dict[str, Any]],
) -> None:
    assert len(usage_rows) == 1
    assert usage_rows[0]["status"] == 499
    assert usage_rows[0]["provider_id"] == "acct-a"
    assert usage_rows[0]["model"] == "m1"

    assert len(outcomes) == 1
    assert outcomes[0]["status"] == 499
    assert outcomes[0]["status"] != 502
    assert outcomes[0]["streamed"] == 1
    assert outcomes[0]["attempts"] == 1
    assert outcomes[0]["provider_id"] == "acct-a"

    assert len(logs) == 1
    assert logs[0]["status"] == 499
    assert logs[0]["model"] == "test/m1"
    assert logs[0]["streamed"] == 1


def _asgi_scope(body: bytes) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 123),
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }


@pytest.mark.asyncio
@respx.mock
async def test_cancelled_stream_persists_usage_outcome_and_log(one_account_app):
    app = one_account_app
    db_path = app.state.db_path
    await set_setting(db_path, "server_request_logging", "true")

    release_upstream = asyncio.Event()
    two_chunks_sent = asyncio.Event()

    async def upstream_lines() -> AsyncIterator[bytes]:
        yield (ROLE_LINE + CONTENT_LINE).encode()
        yield CONTENT_LINE.encode()
        two_chunks_sent.set()
        await release_upstream.wait()

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=upstream_lines(),
            headers={"content-type": "text/event-stream"},
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        send_task = asyncio.create_task(client.post("/v1/chat/completions", json=REQUEST_BODY))
        await asyncio.wait_for(two_chunks_sent.wait(), timeout=10)
        send_task.cancel()
        with suppress(asyncio.CancelledError):
            await send_task

    await _drain_stream_persist_tasks()
    usage_rows, outcomes, logs = await _abort_telemetry_rows(db_path)
    _assert_abort_rows(usage_rows, outcomes, logs)


@pytest.mark.asyncio
@respx.mock
async def test_client_closed_stream_persists_rows_not_502(one_account_app):
    app = one_account_app
    db_path = app.state.db_path
    await set_setting(db_path, "server_request_logging", "true")

    release_upstream = asyncio.Event()
    first_chunk_sent = asyncio.Event()

    async def upstream_lines() -> AsyncIterator[bytes]:
        yield (ROLE_LINE + CONTENT_LINE).encode()
        await release_upstream.wait()

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=upstream_lines(),
            headers={"content-type": "text/event-stream"},
        )
    )

    body = json.dumps(REQUEST_BODY).encode()
    request_body_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_body_delivered
        if not request_body_delivered:
            request_body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await first_chunk_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk_sent.set()
            await release_upstream.wait()

    app_task = asyncio.create_task(app(_asgi_scope(body), receive, send))
    await asyncio.wait_for(first_chunk_sent.wait(), timeout=10)

    with suppress(asyncio.CancelledError):
        await app_task
    del app_task
    gc.collect()
    for _ in range(5):
        await asyncio.sleep(0)
    await _drain_stream_persist_tasks()

    usage_rows, outcomes, logs = await _abort_telemetry_rows(db_path)
    _assert_abort_rows(usage_rows, outcomes, logs)
