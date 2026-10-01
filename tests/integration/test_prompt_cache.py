import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from janus.routing.prompt_cache import prompt_cache_clear
from janus.storage.database import get_connection
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
        default_model="test-m1",
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
        combos=[ComboConfig(name="test-combo", models=["test/test-m1"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture(autouse=True)
def _clean_cache():
    prompt_cache_clear()
    yield
    prompt_cache_clear()


UPSTREAM_RESPONSE = {
    "id": "r1",
    "object": "chat.completion",
    "model": "test-m1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Bonjour!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


def _payload(**overrides) -> dict:
    base = {
        "model": "test/test-m1",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
    }
    base.update(overrides)
    return base


def _mock_upstream() -> respx.Route:
    return respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=UPSTREAM_RESPONSE)
    )


async def _rows(db_path, sql: str) -> list[dict]:
    async with get_connection(db_path) as db:
        async with db.execute(sql) as cur:
            return [dict(row) for row in await cur.fetchall()]


@respx.mock
async def test_second_deterministic_request_is_served_from_cache(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/chat/completions", json=_payload())
        second = await client.post("/v1/chat/completions", json=_payload())

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.headers.get("x-janus-cache") is None
    assert second.headers.get("x-janus-cache") == "hit"
    assert route.call_count == 1


@respx.mock
async def test_cache_hit_records_zero_cost_usage(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload())
        await client.post("/v1/chat/completions", json=_payload())

    usage_rows = await _rows(
        app.state.db_path,
        "SELECT provider_id, input_tokens, output_tokens, cost FROM usage ORDER BY id",
    )
    assert len(usage_rows) == 2
    assert usage_rows[0]["provider_id"] == "test"
    assert usage_rows[1]["provider_id"] == "prompt-cache"
    assert usage_rows[1]["cost"] == 0.0
    assert usage_rows[1]["input_tokens"] == 3
    assert usage_rows[1]["output_tokens"] == 1

    outcomes = await _rows(
        app.state.db_path,
        "SELECT model, provider_id, status FROM request_outcomes ORDER BY id",
    )
    assert len(outcomes) == 2
    assert outcomes[1]["status"] == 200
    assert outcomes[1]["provider_id"] == "prompt-cache"
    assert outcomes[1]["model"] == "test-m1"


@respx.mock
async def test_cache_hit_visible_in_request_logs(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    await set_setting(app.state.db_path, "server_request_logging", "true")
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload())
        await client.post("/v1/chat/completions", json=_payload())

    logs = await _rows(
        app.state.db_path,
        "SELECT model, provider_id, status, response_body FROM request_logs ORDER BY id",
    )
    assert len(logs) == 2
    assert logs[1]["provider_id"] == "prompt-cache"
    assert logs[1]["status"] == 200
    assert "Bonjour!" in logs[1]["response_body"]


@respx.mock
async def test_disabled_by_default_never_caches(app):
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/chat/completions", json=_payload())
        second = await client.post("/v1/chat/completions", json=_payload())

    assert first.headers.get("x-janus-cache") is None
    assert second.headers.get("x-janus-cache") is None
    assert route.call_count == 2


@respx.mock
async def test_absent_temperature_is_not_cached(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload(temperature=None))
        await client.post("/v1/chat/completions", json=_payload(temperature=None))

    assert route.call_count == 2


@respx.mock
async def test_absent_temperature_does_not_hit_zero_temperature_entry(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload())
        await client.post("/v1/chat/completions", json=_payload(temperature=None))

    assert route.call_count == 2


@respx.mock
async def test_pinned_seed_is_cached(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/chat/completions", json=_payload(temperature=None, seed=42))
        second = await client.post("/v1/chat/completions", json=_payload(temperature=None, seed=42))

    assert second.headers.get("x-janus-cache") == "hit"
    assert route.call_count == 1
    assert first.json() == second.json()


@respx.mock
async def test_narrowed_top_p_blocks_caching(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload(top_p=0.9))
        await client.post("/v1/chat/completions", json=_payload(top_p=0.9))

    assert route.call_count == 2


@respx.mock
async def test_streaming_requests_are_never_cached(app):
    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},'
        '"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            async with client.stream(
                "POST", "/v1/chat/completions", json=_payload(stream=True)
            ) as response:
                assert response.status_code == 200
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                assert b"Hello" in body

    assert route.call_count == 2


@respx.mock
async def test_changed_output_shaping_param_misses(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload())
        await client.post("/v1/chat/completions", json=_payload(max_tokens=16))

    assert route.call_count == 2


@respx.mock
async def test_combo_model_hit(app):
    await set_setting(app.state.db_path, "server_prompt_cache_enabled", "true")
    route = _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/chat/completions", json=_payload(model="test-combo"))
        second = await client.post("/v1/chat/completions", json=_payload(model="test-combo"))

    assert second.headers.get("x-janus-cache") == "hit"
    assert second.headers.get("x-janus-resolved-model") == "test-m1"
    assert route.call_count == 1


async def test_dashboard_settings_validation_for_cache_bounds(app):
    from janus.storage.api_keys import create_key

    raw_key, _record = await create_key(app.state.db_path, "cache-settings")
    headers = {"Authorization": f"Bearer {raw_key}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_prompt_cache_ttl_s", "value": "0"},
            headers=headers,
        )
        assert bad.status_code == 400
        ok = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_prompt_cache_ttl_s", "value": "7200"},
            headers=headers,
        )
        assert ok.status_code == 200
        unknown = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_prompt_cache_max_entries", "value": "not-a-number"},
            headers=headers,
        )
        assert unknown.status_code == 400
