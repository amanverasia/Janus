"""Issue #187: one attempt_signals row per real upstream attempt, on every path."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.routing.prompt_cache import prompt_cache_clear
from janus.storage.attempt_signals import (
    list_attempt_signals,
    reset_attempt_signal_prune_throttle,
)
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

ROLE_LINE = (
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
)
CONTENT_LINE = (
    'data: {"id":"r1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
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


def _provider(pid: str, host: str) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        prefix="test",
        api_type="openai_compat",
        base_url=f"https://{host}/v1",
        api_key=f"sk-{pid}",
        models=["m1"],
    )


@pytest.fixture
async def one_account_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[_provider("acct-a", "a.local")],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture
async def two_account_app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[_provider("acct-a", "a.local"), _provider("acct-b", "b.local")],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()
    prompt_cache_clear()
    yield
    prompt_cache_clear()


async def _signals(app):
    await _drain_stream_persist_tasks()
    return sorted(
        await list_attempt_signals(app.state.db_path), key=lambda r: (r["account_id"], r["id"])
    )


def _body(**kw):
    b = {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}
    b.update(kw)
    return b


@respx.mock
async def test_fallback_records_error_then_ok(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(500))
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 200
    rows = await _signals(two_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("error", 500), ("ok", 200)]
    assert rows[0]["account_id"] != rows[1]["account_id"]
    assert rows[1]["output_tokens"] == 4
    assert rows[1]["streamed"] == 0
    assert rows[1]["client_format"] == "openai"
    assert rows[1]["model"] == "m1"


@respx.mock
async def test_network_error_records_null_status(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(side_effect=httpx.ConnectError("boom"))
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        await c.post("http://test/v1/chat/completions", json=_body())
    rows = await _signals(two_account_app)
    assert [x["outcome"] for x in rows] == ["error", "ok"]
    assert rows[0]["status"] is None


@respx.mock
async def test_stream_success_records_ttft(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=GOOD_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body(stream=True))
        assert r.status_code == 200
    rows = await _signals(one_account_app)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["status"] == 200
    assert rows[0]["streamed"] == 1
    assert rows[0]["ttft_ms"] is not None and rows[0]["ttft_ms"] >= 0


@respx.mock
async def test_non_eligible_400_is_client_error(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad"}})
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 400
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("client_error", 400)]


@respx.mock
async def test_exhausted_records_one_row_per_attempt(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(503))
    respx.post("https://b.local/v1/chat/completions").mock(return_value=httpx.Response(503))
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        r = await c.post("http://test/v1/chat/completions", json=_body())
    assert r.status_code == 503
    rows = await _signals(two_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("error", 503), ("error", 503)]


@respx.mock
async def test_unexpected_exception_records_error_signal(one_account_app, monkeypatch):
    import janus.api.routes as routes_mod

    async def explode(*args, **kwargs):
        raise RuntimeError("usage bug")

    monkeypatch.setattr(routes_mod, "record_usage", explode)
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        with suppress(Exception):
            await c.post("http://test/v1/chat/completions", json=_body())
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"]) for x in rows] == [("error", 500)]


@respx.mock
async def test_prompt_cache_hit_records_no_signal(one_account_app):
    await set_setting(one_account_app.state.db_path, "server_prompt_cache_enabled", "true")
    route = respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        first = await c.post("http://test/v1/chat/completions", json=_body(temperature=0))
        second = await c.post("http://test/v1/chat/completions", json=_body(temperature=0))
    assert first.status_code == second.status_code == 200
    assert second.headers.get("x-janus-cache") == "hit"
    assert route.call_count == 1
    rows = await _signals(one_account_app)
    assert len(rows) == route.call_count


@respx.mock
async def test_client_abort_records_aborted_signal(one_account_app):
    app = one_account_app
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
        send_task = asyncio.create_task(
            client.post("/v1/chat/completions", json=_body(model="test/m1", stream=True))
        )
        await asyncio.wait_for(two_chunks_sent.wait(), timeout=10)
        send_task.cancel()
        with suppress(asyncio.CancelledError):
            await send_task

    rows = await _signals(app)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "aborted"
    assert rows[0]["status"] == 499
    assert rows[0]["ttft_ms"] is not None
    assert rows[0]["output_tps"] is None


def _anthropic_body(**kw):
    b = {"model": "m1", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}
    b.update(kw)
    return b


@respx.mock
async def test_canonical_path_records_ok_signal(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/messages", json=_anthropic_body())
    assert r.status_code == 200
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"], x["streamed"]) for x in rows] == [("ok", 200, 0)]
    assert rows[0]["client_format"] == "anthropic"
    assert rows[0]["output_tokens"] == 4


@respx.mock
async def test_canonical_stream_records_ttft(one_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=GOOD_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncClient(transport=ASGITransport(app=one_account_app)) as c:
        r = await c.post("http://test/v1/messages", json=_anthropic_body(stream=True))
        assert r.status_code == 200
    rows = await _signals(one_account_app)
    assert [(x["outcome"], x["status"], x["streamed"]) for x in rows] == [("ok", 200, 1)]
    assert rows[0]["ttft_ms"] is not None


@respx.mock
async def test_canonical_fallback_records_error_then_ok(two_account_app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(429))
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=two_account_app)) as c:
        r = await c.post("http://test/v1/messages", json=_anthropic_body())
    assert r.status_code == 200
    rows = await _signals(two_account_app)
    assert [(x["account_id"], x["outcome"], x["status"]) for x in rows] == [
        (rows[0]["account_id"], "error", 429),
        (rows[1]["account_id"], "ok", 200),
    ]
