import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INVENTORY_PUSH_TOKEN", "test-push-token")
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_push_requires_token(client):
    response = await client.post(
        "/dashboard/api/inventory/push",
        json={"key": "sk-proj-" + "z" * 16},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_push_registers_key(client):
    response = await client.post(
        "/dashboard/api/inventory/push",
        headers={"Authorization": "Bearer test-push-token"},
        json={"key": "sk-proj-" + "a" * 16, "provider": "openai"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["summary"]["registered"] == 1
