"""Fallback consistency for streaming and transport failures (issue #104).

ReadError, WriteError, RemoteProtocolError and other ``httpx.RequestError``
subclasses must rotate to the next account with a cooldown — not surface as a
500. Mid-stream failures must be recorded as a terminal 502 (never a false 200)
and cool the account. 200-wrapped quota/error envelopes must trigger fallback.
Client cancellation must never be treated as an upstream failure.
"""

import asyncio

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting

_GOOD_NONSTREAM = {
    "id": "r1",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}

_GOOD_SSE = (
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


def _two_account_cfg(tmp_path) -> JanusConfig:
    return JanusConfig(
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


@pytest.fixture
async def two_account_app(tmp_path):
    app = create_app(config=_two_account_cfg(tmp_path))
    await _seed_and_reload(app)
    return app


def _handler(app):
    return app.state.fallback_handler


def _request(model="test/m1", stream=False):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": stream,
    }


# ── Pre-stream transport errors rotate to the next account ──────────────


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadError("connection reset"),
        httpx.WriteError("write failed"),
        httpx.RemoteProtocolError("server closed"),
    ],
    ids=["read-error", "write-error", "remote-protocol-error"],
)
async def test_transport_error_rotates_to_next_account(two_account_app, exc):
    respx.post("https://a.local/v1/chat/completions").mock(side_effect=exc)
    route_b = respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_GOOD_NONSTREAM)
    )

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        r = await client.post("/v1/chat/completions", json=_request())

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "OK"
    assert route_b.called
    # The failed account must be cooled down so subsequent requests skip it.
    assert not _handler(two_account_app).is_available("acct-a", "m1")


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_streaming_rotates_to_next_account(two_account_app):
    """A transport error before any stream bytes must fall back, not 500."""
    respx.post("https://a.local/v1/chat/completions").mock(
        side_effect=httpx.RemoteProtocolError("connection reset mid-handshake")
    )
    route_b = respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_GOOD_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        async with client.stream(
            "POST", "/v1/chat/completions", json=_request(stream=True)
        ) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk

    assert route_b.called
    assert b"OK" in body
    assert not _handler(two_account_app).is_available("acct-a", "m1")


# ── 200-wrapped quota/error envelopes trigger fallback ─────────────────


@pytest.mark.asyncio
@respx.mock
async def test_200_wrapped_quota_error_rotates_to_next_account(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"error": "quota exceeded"})
    )
    route_b = respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_GOOD_NONSTREAM)
    )

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        r = await client.post("/v1/chat/completions", json=_request())

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "OK"
    assert route_b.called
    assert not _handler(two_account_app).is_available("acct-a", "m1")


@pytest.mark.asyncio
@respx.mock
async def test_200_wrapped_rate_limit_body_rotates_to_next_account(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"error": {"message": "Too many requests, slow down"}}
        )
    )
    route_b = respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_GOOD_NONSTREAM)
    )

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        r = await client.post("/v1/chat/completions", json=_request())

    assert r.status_code == 200
    assert route_b.called
    assert not _handler(two_account_app).is_available("acct-a", "m1")


# ── Partial stream failure: accurate 502 + cooldown ─────────────────────


@pytest.fixture
async def single_account_app(tmp_path):
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
    await set_setting(app.state.db_path, "server_request_logging", "true")
    return app


async def _failing_stream_sse():
    """Yield one valid SSE chunk, then raise mid-stream."""
    yield (
        b'data: {"id":"r1","object":"chat.completion.chunk",'
        b'"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    )
    raise httpx.RemoteProtocolError("upstream closed connection mid-stream")


@pytest.mark.asyncio
@respx.mock
async def test_partial_stream_failure_records_502_and_cools_account(single_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=_failing_stream_sse(),
            headers={"content-type": "text/event-stream"},
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=single_account_app), base_url="http://test"
    ) as client:
        async with client.stream(
            "POST", "/v1/chat/completions", json=_request(stream=True)
        ) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk

    # The client received the partial chunk before the upstream died.
    assert b"partial" in body

    # The account that failed mid-stream must be cooled for future requests.
    assert not _handler(single_account_app).is_available("acct-a", "m1")

    # The request log must record an accurate terminal failure (502), not a
    # false 200 success.
    logs = await list_request_logs(single_account_app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 502


async def _parser_failing_sse():
    """Yield one valid SSE chunk, then raise a non-transport error mid-stream."""
    yield (
        b'data: {"id":"r1","object":"chat.completion.chunk",'
        b'"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
    )
    raise ValueError("malformed upstream chunk")


@pytest.mark.asyncio
@respx.mock
async def test_parser_failure_records_502_without_cooling(single_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=_parser_failing_sse(),
            headers={"content-type": "text/event-stream"},
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=single_account_app), base_url="http://test"
    ) as client:
        async with client.stream(
            "POST", "/v1/chat/completions", json=_request(stream=True)
        ) as response:
            assert response.status_code == 200
            async for _ in response.aiter_bytes():
                pass

    # A parser/protocol failure is a terminal failure (502), but it is not the
    # account's fault — the account must stay available for the next request.
    assert _handler(single_account_app).is_available("acct-a", "m1")

    logs = await list_request_logs(single_account_app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 502


# ── Client cancellation is not an upstream failure ──────────────────────


async def _slow_sse():
    """Yield chunks with a yield between each so the client can cancel mid-stream."""
    for text in ("alpha", "beta", "gamma"):
        yield (
            'data: {"id":"r1","object":"chat.completion.chunk",'
            f'"choices":[{{"index":0,"delta":{{"content":"{text}"}},"finish_reason":null}}]}}\n\n'
        ).encode()
        await asyncio.sleep(0.05)
    yield b"data: [DONE]\n\n"


@pytest.mark.asyncio
@respx.mock
async def test_client_cancellation_does_not_cool_account(single_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_slow_sse(), headers={"content-type": "text/event-stream"}
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=single_account_app), base_url="http://test"
    ) as client:
        task = asyncio.create_task(_stream_one_chunk_then_cancel(client, _request(stream=True)))
        await task

    # Let the server-side finally blocks finish recording usage/logs.
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    # Client cancellation must NOT cool the account — it is not an upstream
    # failure, so the account stays available for the next request.
    assert _handler(single_account_app).is_available("acct-a", "m1")


async def _stream_one_chunk_then_cancel(client, payload) -> None:
    async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
        assert response.status_code == 200
        async for _ in response.aiter_bytes():
            break  # read one chunk then stop → triggers client cancellation
