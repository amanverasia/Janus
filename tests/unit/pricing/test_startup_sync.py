from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from janus.app import _initial_pricing_sync, _pricing_catalog_needs_sync, create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.pricing.sync import LITELLM_URL, OPENROUTER_URL
from janus.storage.database import init_db, seed_from_config
from janus.storage.pricing_catalog import replace_catalog
from janus.storage.settings import set_setting


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


async def _init(app):
    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, app.state.config)


async def test_needs_sync_when_catalog_empty(app):
    await _init(app)
    assert await _pricing_catalog_needs_sync(app) is True


async def test_no_sync_needed_when_catalog_fresh(app, monkeypatch):
    monkeypatch.setenv("PRICING_SYNC_INTERVAL_HOURS", "24")
    await _init(app)
    await replace_catalog(
        app.state.db_path,
        [
            {
                "model": "m",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )
    await set_setting(app.state.db_path, "pricing_last_sync_at", datetime.now(UTC).isoformat())
    assert await _pricing_catalog_needs_sync(app) is False


async def test_needs_sync_when_stale(app, monkeypatch):
    monkeypatch.setenv("PRICING_SYNC_INTERVAL_HOURS", "24")
    await _init(app)
    await replace_catalog(
        app.state.db_path,
        [
            {
                "model": "m",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )
    stale = datetime.now(UTC) - timedelta(hours=48)
    await set_setting(app.state.db_path, "pricing_last_sync_at", stale.isoformat())
    assert await _pricing_catalog_needs_sync(app) is True


async def test_needs_sync_when_last_sync_unparseable(app):
    await _init(app)
    await replace_catalog(
        app.state.db_path,
        [
            {
                "model": "m",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )
    await set_setting(app.state.db_path, "pricing_last_sync_at", "not-a-date")
    assert await _pricing_catalog_needs_sync(app) is True


@respx.mock
async def test_initial_pricing_sync_populates_registry(app):
    await _init(app)
    respx.get(LITELLM_URL).mock(
        return_value=httpx.Response(
            200,
            json={"m": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}},
        )
    )
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    from janus.dashboard.reload import reload_pricing

    await reload_pricing(app)
    assert app.state.pricing_registry.get("m") is None

    await _initial_pricing_sync(app)

    assert app.state.pricing_registry.get("m") is not None


@respx.mock
async def test_initial_pricing_sync_fails_open_on_error(app):
    await _init(app)
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(500))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))

    from janus.dashboard.reload import reload_pricing

    await reload_pricing(app)

    # Must not raise even though both sources fail.
    await _initial_pricing_sync(app)
