import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from tests.fixtures.dashboard_auth import with_dashboard_auth


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INVENTORY_PUSH_TOKEN", "test-push-token")
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return with_dashboard_auth(create_app(config=cfg))


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


@pytest.mark.asyncio
async def test_push_rejects_wrong_and_non_ascii_tokens(client):
    for header in ("Bearer wrong-token", "Bearer tést-push-tökén", "Bearer ", "test-push-token"):
        response = await client.post(
            "/dashboard/api/inventory/push",
            headers={"Authorization": header.encode("utf-8")},
            json={"key": "sk-proj-" + "b" * 16, "provider": "openai"},
        )
        assert response.status_code == 401, header


def test_push_token_uses_constant_time_comparison():
    import inspect

    from janus.inventory import push_auth

    source = inspect.getsource(push_auth.require_inventory_push_token)
    assert "compare_digest" in source
    assert "token != expected" not in source


@pytest.mark.asyncio
async def test_push_errors_are_sanitized(client):
    response = await client.post(
        "/dashboard/api/inventory/push",
        headers={"Authorization": "Bearer test-push-token"},
        json={"key": "sk-proj-" + "c" * 16, "provider": "nosuchprovider"},
    )
    assert response.status_code in {200, 201}
    payload = response.json()
    rejected = [r for r in payload["results"] if r["status"] == "rejected"]
    assert rejected
    message = str(rejected[0].get("error"))
    assert "nosuchprovider" not in message
    assert "Provider is not recognized." in message
