"""Guards against wasted bytes in dashboard state payloads (issue #111).

Measured on a production deployment with real data:

  models    1,205,756 B   99.7% one array of 1,714 rows
  pricing     828,927 B   98.8% catalog, 4,828 rows
  providers   127,749 B   `catalog` and `catalog_presets` byte-identical
  inventory    38,808 B   `top_keys` 47.9%, never read by the frontend

Compression shrank the transfer, but the server still builds and serialises
every byte and the browser still parses it, so duplicated and unread sections
are pure cost.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

AUTH_KEY = "payload-shape-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
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
                api_key="provider-secret",
                models=[f"model-{i}" for i in range(30)],
            )
        ],
        api_keys=[AUTH_KEY],
    )
    return create_app(config=config)


def remote_transport(app):
    return ASGITransport(app=app, client=("203.0.113.10", 4321))


async def _state(client, section: str) -> dict:
    response = await client.get(f"/dashboard/api/v2/state/{section}", headers=AUTH_HEADERS)
    assert response.status_code == 200, section
    return response.json()["data"]


async def test_provider_catalog_is_not_sent_twice(app) -> None:
    """`catalog` and `catalog_presets` were the same object under two keys.

    On production that duplicated 33,640 B -- 25% of the providers payload.
    """
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        data = await _state(client, "providers")

    assert "catalog" in data, "the provider catalog must still be served"
    assert "catalog_presets" not in data, "catalog_presets duplicated catalog verbatim"


async def test_inventory_does_not_send_unread_top_keys(app) -> None:
    """`top_keys` was 47.9% of the inventory payload and no page ever read it.

    Building it also cost a database query on every inventory page load.
    """
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        data = await _state(client, "inventory")

    assert "top_keys" not in data
    # the sections the page does render must survive
    for key in ("summary", "provider_cards", "recent_activity", "credit_summary", "best_keys"):
        assert key in data, key


async def test_no_state_section_serves_a_duplicated_top_level_section(app) -> None:
    """A general guard: two top-level keys holding identical non-trivial values.

    This is what `catalog`/`catalog_presets` looked like, and it is cheap to
    reintroduce by accident when a field is renamed compatibly.
    """
    sections = ("overview", "analytics", "inventory", "models", "providers", "pricing", "routing")
    offenders: list[str] = []
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        for section in sections:
            data = await _state(client, section)
            seen: dict[str, str] = {}
            for key, value in data.items():
                blob = json.dumps(value, sort_keys=True, default=str)
                if len(blob) < 200:
                    continue
                if blob in seen:
                    offenders.append(f"{section}: {seen[blob]} == {key} ({len(blob)} B)")
                seen[blob] = key
    assert not offenders, offenders
