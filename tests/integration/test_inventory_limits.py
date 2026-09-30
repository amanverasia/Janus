from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard import inventory_request
from janus.inventory.rate_limit import SubmitRateLimiter
from tests.fixtures.dashboard_auth import with_dashboard_auth


@pytest.fixture
def app(tmp_path):
    return with_dashboard_auth(
        create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    )


@pytest.mark.parametrize("path", ["submit", "push", "keys/bulk/recheck", "import", "preview"])
async def test_inventory_rejects_declared_oversize_before_reading(app, monkeypatch, path):
    monkeypatch.setattr(inventory_request, "MAX_INVENTORY_BODY_BYTES", 16)
    monkeypatch.setattr(inventory_request, "MAX_INVENTORY_IMPORT_BYTES", 16)
    consumed = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed
        consumed = True
        yield b"x" * 17

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/dashboard/api/inventory/{path}",
            headers={"Content-Length": "17"},
            content=body(),
        )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
    assert not consumed
    assert not app.state.db_path.exists()


@pytest.mark.parametrize("path", ["submit", "push", "import", "preview"])
async def test_inventory_rejects_chunked_oversize_without_consuming_rest(app, monkeypatch, path):
    monkeypatch.setattr(inventory_request, "MAX_INVENTORY_BODY_BYTES", 16)
    monkeypatch.setattr(inventory_request, "MAX_INVENTORY_IMPORT_BYTES", 16)
    consumed_rest = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed_rest
        yield b"x" * 10
        yield b"x" * 10
        consumed_rest = True
        yield b"x" * 100

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/dashboard/api/inventory/{path}", content=body())
    assert response.status_code == 413
    assert not consumed_rest
    assert not app.state.db_path.exists()


@pytest.mark.parametrize("trusted, expected", [(True, [200, 200]), (False, [200, 429])])
async def test_rate_limit_uses_client_resolved_by_trusted_proxy(trusted, expected):
    from fastapi import FastAPI, Request

    app = FastAPI()
    limiter = SubmitRateLimiter(limit=1)

    @app.post("/")
    async def submit(request: Request):
        from fastapi.responses import JSONResponse

        allowed = limiter.allow(request.client.host)
        return JSONResponse({}, status_code=200 if allowed else 429)

    proxy = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1" if trusted else "192.0.2.1")
    transport = ASGITransport(app=proxy, client=("127.0.0.1", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.post("/", headers={"X-Forwarded-For": address})
            for address in ("198.51.100.1", "198.51.100.2")
        ]
    assert [response.status_code for response in responses] == expected
