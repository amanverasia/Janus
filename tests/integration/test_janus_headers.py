"""x-janus-* observability headers on success, streaming, fallback, and errors."""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.outcomes import list_request_outcomes
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting

SECRET_MARKERS = ("sk-a", "sk-b", "acct-a", "acct-b")


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


def _ok_sse() -> httpx.Response:
    sse = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
        "data: [DONE]\n\n"
    )
    return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "test/m1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    payload.update(overrides)
    return payload


def _assert_no_secrets(response: httpx.Response) -> None:
    for name, value in response.headers.items():
        for marker in SECRET_MARKERS:
            assert marker not in value, f"secret marker {marker!r} leaked via {name}"


@pytest.mark.asyncio
@respx.mock
async def test_non_streaming_success_headers(app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=_ok_json())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload())

    assert r.status_code == 200
    assert r.headers["x-janus-requested-model"] == "test/m1"
    assert r.headers["x-janus-resolved-model"] == "m1"
    assert "x-janus-fallback" not in r.headers
    assert "x-janus-fallback-attempt" not in r.headers
    assert "x-janus-error-type" not in r.headers
    assert "sk-" not in r.headers.get("x-janus-saver", "")
    _assert_no_secrets(r)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_success_headers_set_before_first_byte(app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=_ok_sse())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("POST", "/v1/chat/completions", json=_payload(stream=True)) as r:
            assert r.status_code == 200
            assert r.headers["x-janus-requested-model"] == "test/m1"
            assert r.headers["x-janus-resolved-model"] == "m1"
            assert "x-janus-fallback" not in r.headers
            _assert_no_secrets(r)
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
    assert b"OK" in body


@pytest.mark.asyncio
@respx.mock
async def test_fallback_attempt_headers(app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(return_value=_ok_json())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload())

    assert r.status_code == 200
    assert r.headers["x-janus-fallback"] == "true"
    assert r.headers["x-janus-fallback-attempt"] == "2"
    assert r.headers["x-janus-resolved-model"] == "m1"
    _assert_no_secrets(r)


@pytest.mark.asyncio
@respx.mock
async def test_saver_header_names_applied_savers(app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=_ok_json())
    respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(
            200, json={"messages": [{"role": "user", "content": "compressed"}]}
        )
    )
    await set_setting(app.state.db_path, "saver_headroom_enabled", "true")
    from janus.dashboard.reload import reload_savers

    await reload_savers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json=_payload(messages=[{"role": "user", "content": "a long prompt " * 60}]),
        )

    assert r.status_code == 200
    assert "HeadroomSaver" in r.headers["x-janus-saver"]
    _assert_no_secrets(r)


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_error_headers(app):
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload())

    assert r.status_code == 503
    assert r.headers["x-janus-error-type"] == "all_providers_exhausted"
    assert r.headers["x-janus-requested-model"] == "test/m1"
    _assert_no_secrets(r)


@pytest.mark.asyncio
@respx.mock
async def test_request_logs_and_headers_share_truth_source(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    respx.post("https://a.local/v1/chat/completions").mock(return_value=_ok_json())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload())

    assert r.status_code == 200
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["model"] == r.headers["x-janus-requested-model"]
    outcomes = await list_request_outcomes(app.state.db_path)
    assert len(outcomes) == 1
    assert outcomes[0]["model"] == r.headers["x-janus-resolved-model"]
    assert outcomes[0]["attempts"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_unroutable_model_error_headers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload(model="nope/m1"))

    assert r.status_code == 400
    assert r.headers["x-janus-error-type"] == "no_available_providers"
    assert r.headers["x-janus-requested-model"] == "nope/m1"
    _assert_no_secrets(r)


@pytest.mark.asyncio
@respx.mock
async def test_non_ascii_model_name_never_crashes_headers(app):
    respx.post("https://a.local/v1/chat/completions").mock(return_value=_ok_json())
    weird = "test/m🎉1\ninjected: value"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", json=_payload(model=weird))

    assert r.status_code == 200
    assert r.headers["x-janus-requested-model"] == "test/m?1?injected: value"
    assert "\n" not in r.headers["x-janus-requested-model"]
