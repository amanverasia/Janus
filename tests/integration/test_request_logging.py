import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.request_logs import get_request_log, list_request_logs, record_request_log
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
    provider = ProviderConfig(
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _mock_upstream() -> None:
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "model": "test-m1",
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
    )


@pytest.mark.asyncio
@respx.mock
async def test_disabled_by_default_records_nothing(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
    assert await list_request_logs(app.state.db_path) == []


@pytest.mark.asyncio
@respx.mock
async def test_enabled_records_request_and_response(app):
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["client_format"] == "openai"
    assert logs[0]["model"] == "test/test-m1"
    assert logs[0]["provider_id"] == "test"
    assert logs[0]["status"] == 200
    assert logs[0]["streamed"] == 0
    detail = await get_request_log(app.state.db_path, logs[0]["id"])
    assert detail is not None
    assert '"hi"' in detail["request_body"]
    assert "Hello!" in detail["response_body"]


@pytest.mark.asyncio
@respx.mock
async def test_enabled_records_streaming(app):
    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "model": "test/test-m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            assert response.status_code == 200
            async for _ in response.aiter_bytes():
                pass
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["streamed"] == 1
    assert logs[0]["status"] == 200


@pytest.mark.asyncio
async def test_enabled_records_exhausted_error(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "rate limited"})
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
            r = await client.post("/v1/chat/completions", json=payload)
            assert r.status_code == 503
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 503
    assert "exhausted" in logs[0]["error"]


@pytest.mark.asyncio
@respx.mock
async def test_enabled_records_non_fallback_upstream_error(app):
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 400
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 400
    assert logs[0]["provider_id"] == "test"
    assert logs[0]["error"]


@pytest.mark.asyncio
@respx.mock
async def test_dashboard_page_and_clear(app):
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        await client.post("/v1/chat/completions", json=payload)

        r = await client.get("/dashboard/request-logs")
        assert r.status_code == 200
        assert "test/test-m1" in r.text

        r = await client.get("/dashboard/api/request-logs/export")
        assert r.status_code == 200
        assert r.json()[0]["model"] == "test/test-m1"

        r = await client.delete("/dashboard/api/request-logs")
        assert r.status_code == 200
    assert await list_request_logs(app.state.db_path) == []


@pytest.mark.asyncio
async def test_request_logs_partial_pagination(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    for i in range(3):
        await record_request_log(
            app.state.db_path, client_format="openai", model=f"m{i}", status=200
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/request-logs/partial?limit=2&offset=0")
        assert r.status_code == 200
        assert "Showing" in r.text
        assert "m2" in r.text or "m1" in r.text
        r2 = await client.get("/dashboard/api/request-logs/partial?limit=2&offset=2")
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_page_shows_disabled_banner(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/request-logs")
        assert r.status_code == 200
        assert "disabled" in r.text


@pytest.mark.asyncio
@respx.mock
async def test_labeled_key_records_client_key_label(tmp_path):
    provider = ProviderConfig(
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=True, data_dir=tmp_path),
        providers=[provider],
        api_keys=["sk-static-labeled"],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer sk-static-labeled"},
        )
        assert r.status_code == 200
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["client_key_label"] is not None
    assert logs[0]["client_key_label"].startswith("Config (")
    assert logs[0]["client_key_id"] is None


@pytest.mark.asyncio
@respx.mock
async def test_db_key_records_client_key_id(tmp_path):
    from janus.storage.api_keys import create_key

    provider = ProviderConfig(
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=True, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    full_key, key_info = await create_key(app.state.db_path, "my-app")
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert r.status_code == 200
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["client_key_id"] == key_info["id"]
    assert logs[0]["client_key_label"] is None
    assert logs[0]["client_key_name"] == "my-app"


@pytest.mark.asyncio
@respx.mock
async def test_db_key_name_shown_on_dashboard(tmp_path):
    from janus.storage.api_keys import create_key

    provider = ProviderConfig(
        id="test",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-test",
        models=["test-m1"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=True, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    full_key, _ = await create_key(app.state.db_path, "alice-laptop")
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert r.status_code == 200

        page = await client.get("/dashboard/request-logs")
        assert page.status_code == 200
        assert "alice-laptop" in page.text
        assert "key #" not in page.text


@pytest.mark.asyncio
async def test_enabled_records_model_not_allowed(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    from janus.storage.api_keys import create_key

    await set_setting(app.state.db_path, "server_require_api_key", "true")
    key, _ = await create_key(
        app.state.db_path, name="scoped", can_login=False, allowed_models=["other/*"]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 403
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 403


@pytest.mark.asyncio
@respx.mock
async def test_anonymous_access_records_null_client_key(app):
    _mock_upstream()
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

        logs = await list_request_logs(app.state.db_path)
        assert len(logs) == 1
        assert logs[0]["client_key_id"] is None
        assert logs[0]["client_key_label"] is None

        page = await client.get("/dashboard/request-logs")
        assert page.status_code == 200
        assert "—" in page.text


@pytest.mark.asyncio
async def test_enabled_records_unknown_model(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "nope/missing", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 400
    logs = await list_request_logs(app.state.db_path)
    assert any(log["status"] == 400 for log in logs)
