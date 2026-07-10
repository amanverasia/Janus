import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.pricing.sync import LITELLM_URL, OPENROUTER_URL
from janus.storage.usage import record_usage


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


def _litellm_payload():
    return {
        "sample_spec": {"input_cost_per_token": 1, "output_cost_per_token": 1, "mode": "chat"},
        "litellm-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }


def _openrouter_payload():
    return {
        "data": [
            {
                "id": "or-provider/or-model",
                "pricing": {"prompt": "0.000003", "completion": "0.000004"},
            }
        ]
    }


@pytest.mark.asyncio
async def test_pricing_page_shows_never_synced_notice(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/pricing")
        assert r.status_code == 200
        assert "never been synced" in r.text


@pytest.mark.asyncio
async def test_pricing_page_lists_unpriced_models(app):
    db_path = app.state.db_path
    from janus.storage.database import init_db

    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="p",
        model="totally-unknown-model",
        input_tokens=100,
        output_tokens=50,
        status=200,
        cost=0.0,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/pricing")
        assert r.status_code == 200
        assert "Unpriced models seen recently" in r.text
        assert "totally-unknown-model" in r.text


@pytest.mark.asyncio
async def test_pricing_page_excludes_priced_models_from_unpriced_table(app):
    db_path = app.state.db_path
    from janus.storage.database import init_db

    await init_db(db_path)
    # This model resolves via builtin pricing, so even $0-cost rows shouldn't
    # surface it as "unpriced" -- the handler filters by the live registry.
    await record_usage(
        db_path,
        provider_id="p",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        status=200,
        cost=0.0,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/pricing")
        assert r.status_code == 200
        assert "Unpriced models seen recently" not in r.text


@pytest.mark.asyncio
@respx.mock
async def test_sync_endpoint_success_updates_catalog_and_page(app):
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(200, json=_litellm_payload()))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=_openrouter_payload()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/pricing/sync")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 3
        assert data["synced_at"] is not None

        page = await client.get("/dashboard/pricing")
        assert page.status_code == 200
        assert "litellm-model" in page.text
        assert "3 models" in page.text


@pytest.mark.asyncio
@respx.mock
async def test_sync_endpoint_failure_returns_502_and_page_still_renders(app):
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(500))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/pricing/sync")
        assert r.status_code == 502
        assert "error" in r.json()

        page = await client.get("/dashboard/pricing")
        assert page.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_sync_then_override_shows_source_badges(app):
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(200, json=_litellm_payload()))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=_openrouter_payload()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sync_resp = await client.post("/dashboard/api/pricing/sync")
        assert sync_resp.status_code == 200

        override_resp = await client.post(
            "/dashboard/api/pricing",
            data={
                "model": "litellm-model",
                "input_per_mtok": "9.99",
                "output_per_mtok": "19.99",
            },
        )
        assert override_resp.status_code == 200

        page = await client.get("/dashboard/pricing")
        assert page.status_code == 200
        assert "override" in page.text
        assert "catalog" in page.text
        assert "builtin" in page.text
