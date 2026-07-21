import re

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.database import init_db
from janus.storage.settings import set_setting


@pytest.fixture
async def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    app = create_app(config=cfg)
    await init_db(app.state.db_path)
    return app


async def _set_limit(app, limit: int) -> None:
    await set_setting(app.state.db_path, "server_gateway_rate_limit_rpm", str(limit))


async def test_gateway_limit_by_anonymous_ip(app) -> None:
    await _set_limit(app, 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/v1/models")
        blocked = await client.get("/v1/models")

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert blocked.headers["x-ratelimit-limit"] == "1"
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert blocked.headers["x-ratelimit-reset"]
    assert blocked.json()["detail"]["error"]["type"] == "rate_limit_exceeded"


async def test_gateway_limit_covers_post_traffic(app) -> None:
    await _set_limit(app, 1)
    payload = {"model": "missing", "messages": [{"role": "user", "content": "hello"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/chat/completions", json=payload)
        blocked = await client.post("/v1/chat/completions", json=payload)

    assert first.status_code != 429
    assert blocked.status_code == 429


async def test_gateway_limit_isolated_per_db_key(app) -> None:
    await set_setting(app.state.db_path, "server_require_api_key", "true")
    await _set_limit(app, 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_create = await client.post("/dashboard/api/keys", data={"name": "first"})
        second_create = await client.post("/dashboard/api/keys", data={"name": "second"})
        first_key = re.search(r"sk-janus-[a-f0-9]+", first_create.text).group(0)
        second_key = re.search(r"sk-janus-[a-f0-9]+", second_create.text).group(0)

        assert (
            await client.get("/v1/models", headers={"Authorization": f"Bearer {first_key}"})
        ).status_code == 200
        assert (
            await client.get("/v1/models", headers={"Authorization": f"Bearer {first_key}"})
        ).status_code == 429
        assert (
            await client.get("/v1/models", headers={"Authorization": f"Bearer {second_key}"})
        ).status_code == 200


async def test_gateway_limit_isolated_per_static_key(tmp_path) -> None:
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=True, data_dir=tmp_path),
        api_keys=["static-a", "static-b"],
    )
    app = create_app(config=cfg)
    await init_db(app.state.db_path)
    await _set_limit(app, 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.get("/v1/models", headers={"Authorization": "Bearer static-a"})
        ).status_code == 200
        assert (
            await client.get("/v1/models", headers={"Authorization": "Bearer static-a"})
        ).status_code == 429
        assert (
            await client.get("/v1/models", headers={"Authorization": "Bearer static-b"})
        ).status_code == 200


async def test_gateway_limit_covers_gemini_and_ollama_surfaces(app) -> None:
    await _set_limit(app, 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        gemini = await client.post("/v1beta/models/test:unknown", json={})
        ollama = await client.get("/api/tags")

    assert gemini.status_code == 404
    assert ollama.status_code == 429


async def test_gateway_limit_setting_takes_effect_immediately(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/v1/models")).status_code == 200
        await _set_limit(app, 1)
        assert (await client.get("/v1/models")).status_code == 200
        assert (await client.get("/v1/models")).status_code == 429


@pytest.mark.parametrize(
    ("value", "message"),
    [("-1", "must be >= 0"), ("100001", "must be <= 100000")],
)
async def test_gateway_limit_setting_rejects_out_of_range_values(
    app, value: str, message: str
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_gateway_rate_limit_rpm", "value": value},
        )

    assert response.status_code == 400
    assert message in response.text


async def test_gateway_limit_exempts_health_version_and_dashboard(app) -> None:
    await _set_limit(app, 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            assert (await client.get("/v1/health")).status_code == 200
            assert (await client.get("/api/version")).status_code == 200
            assert (await client.get("/dashboard")).status_code == 200
