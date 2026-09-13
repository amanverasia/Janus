"""Server-side pagination for the pricing catalog (issue #111).

The catalog measured 828,927 B / 4,828 rows on a production deployment, 98.8%
of the pricing payload, and the page rendered every row -- 4,828 DOM nodes with
no pagination. The catalog is the only oversized part: `builtin` was 4,874 B and
`overrides` 699 B, so only the catalog is paged.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.pricing_catalog import replace_catalog

AUTH_KEY = "pricing-page-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
CATALOG_ROWS = 1200
pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="test-provider",
                prefix="test",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="secret",
                models=["model-1"],
            )
        ],
        api_keys=[AUTH_KEY],
    )
    return create_app(config=config)


def remote_transport(app):
    return ASGITransport(app=app, client=("203.0.113.10", 4321))


async def _seed(app) -> None:
    """A catalog large enough that paging is observable."""
    await replace_catalog(
        app.state.db_path,
        [
            {
                "model": f"vendor-{i:04d}/model",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "catalog",
            }
            for i in range(CATALOG_ROWS)
        ],
    )


async def _pricing(client, **params):
    response = await client.get(
        "/dashboard/api/v2/state/pricing", params=params, headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    return response.json()


async def test_catalog_is_paged_rather_than_sent_whole(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await _seed(app)
        body = await _pricing(client, limit=50, offset=0)

    assert len(body["data"]["catalog"]) == 50, "the catalog must honour limit"
    pagination = body["meta"]["pagination"]
    assert pagination["total"] == CATALOG_ROWS
    assert pagination["limit"] == 50
    assert pagination["offset"] == 0


async def test_offset_moves_through_the_catalog_without_overlap(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await _seed(app)
        first = await _pricing(client, limit=50, offset=0)
        second = await _pricing(client, limit=50, offset=50)

    a = [row["model"] for row in first["data"]["catalog"]]
    b = [row["model"] for row in second["data"]["catalog"]]
    assert not set(a) & set(b), "pages must not overlap"
    assert a == sorted(a) and b == sorted(b), "ordering must stay stable across pages"
    assert a[-1] < b[0], "page two must continue where page one stopped"


async def test_catalog_search_filters_server_side(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await _seed(app)
        body = await _pricing(client, search="vendor-0007", limit=50, offset=0)

    rows = body["data"]["catalog"]
    assert rows, "search must find the seeded model"
    assert all("vendor-0007" in row["model"] for row in rows)
    assert body["meta"]["pagination"]["total"] == len(rows), "total must reflect the filter"


async def test_small_sections_are_still_sent_whole(app) -> None:
    """`builtin` and `overrides` are small; paging them would only add clicks."""
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await _seed(app)
        body = await _pricing(client, limit=10, offset=0)

    data = body["data"]
    assert isinstance(data["builtin"], list) and data["builtin"], "builtin must be complete"
    assert "overrides" in data
    assert "unpriced" in data, "unpriced is asserted by test_pricing_dashboard.py"


async def test_pricing_payload_stays_small_with_a_large_catalog(app) -> None:
    """The budget this issue asks for: a real-size catalog must not blow up the payload."""
    import json

    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await _seed(app)
        body = await _pricing(client, limit=50, offset=0)

    encoded = len(json.dumps(body, separators=(",", ":")))
    # 1,200 rows unpaged is ~200 KB; one page must stay far below that.
    assert encoded < 60_000, f"pricing payload grew to {encoded:,} B for one page of catalog"
