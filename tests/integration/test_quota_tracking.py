import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    return create_app(config=cfg)


def _mock_upstream() -> None:
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
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
    )


async def _create_quota_provider(client: AsyncClient, limit: int = 2) -> None:
    r = await client.post(
        "/dashboard/api/providers",
        data={
            "id": "sub",
            "prefix": "sub",
            "api_type": "openai_compat",
            "base_url": "https://fake.local/v1",
            "api_key": "sk-test",
            "models": "m1",
            "quota_window": "daily",
            "quota_limit": str(limit),
            "quota_metric": "requests",
        },
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_quota_provider_created_and_tracked(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _create_quota_provider(client, limit=2)

        payload = {"model": "sub/m1", "messages": [{"role": "user", "content": "hi"}]}
        for _ in range(3):
            r = await client.post("/v1/chat/completions", json=payload)
            assert r.status_code == 200

        handler = app.state.fallback_handler
        assert handler.quota_used("sub", "daily") == 3
        target = handler.resolve_attempts("sub/m1")[0]
        assert not handler.has_quota_headroom(target)


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_quota_deprioritizes_but_never_blocks(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _create_quota_provider(client, limit=1)
        payload = {"model": "sub/m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_quota_seeded_after_reload(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _create_quota_provider(client, limit=5)
        payload = {"model": "sub/m1", "messages": [{"role": "user", "content": "hi"}]}
        await client.post("/v1/chat/completions", json=payload)
        await client.post("/v1/chat/completions", json=payload)

        from janus.dashboard.reload import reload_providers

        await reload_providers(app)
        assert app.state.fallback_handler.quota_used("sub", "daily") == 2


@pytest.mark.asyncio
@respx.mock
async def test_providers_page_shows_quota(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _create_quota_provider(client, limit=10)
        payload = {"model": "sub/m1", "messages": [{"role": "user", "content": "hi"}]}
        await client.post("/v1/chat/completions", json=payload)

        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "Quota (daily)" in r.text
        assert "1 / 10 requests" in r.text
        assert "resets in" in r.text


@pytest.mark.asyncio
@respx.mock
async def test_update_provider_quota_fields(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _create_quota_provider(client, limit=2)
        r = await client.put(
            "/dashboard/api/providers/sub",
            data={
                "prefix": "sub",
                "api_type": "openai_compat",
                "base_url": "https://fake.local/v1",
                "api_key": "",
                "models": "m1",
                "quota_window": "monthly",
                "quota_limit": "1000000",
                "quota_metric": "tokens",
            },
        )
        assert r.status_code == 200

        from janus.storage.providers_db import get_provider

        row = await get_provider(app.state.db_path, "sub")
        assert row["quota_window"] == "monthly"
        assert row["quota_limit"] == 1000000
        assert row["quota_metric"] == "tokens"
        assert row["api_key"] == "sk-test"


@pytest.mark.asyncio
@respx.mock
async def test_invalid_quota_params_stored_as_none(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/providers",
            data={
                "id": "noq",
                "prefix": "noq",
                "api_type": "openai_compat",
                "base_url": "https://fake.local/v1",
                "api_key": "sk-test",
                "models": "m1",
                "quota_window": "hourly",
                "quota_limit": "-5",
                "quota_metric": "requests",
            },
        )
        assert r.status_code == 200

        from janus.storage.providers_db import get_provider

        row = await get_provider(app.state.db_path, "noq")
        assert row["quota_window"] is None
        assert row["quota_limit"] is None
