import pytest

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.reload import reload_pricing
from janus.storage.database import init_db, seed_from_config
from janus.storage.pricing_catalog import replace_catalog


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(config=cfg)


async def test_reload_pricing_picks_up_catalog_rows(app):
    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)

    await replace_catalog(
        db_path,
        [
            {
                "model": "catalog-model",
                "input_per_mtok": 7.0,
                "output_per_mtok": 8.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )

    await reload_pricing(app)

    registry = app.state.pricing_registry
    pricing = registry.get("catalog-model")
    assert pricing is not None
    assert pricing.input_per_mtok == 7.0
    assert registry.source_of("catalog-model") == "catalog"


async def test_reload_pricing_override_beats_catalog(app):
    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)

    await replace_catalog(
        db_path,
        [
            {
                "model": "shared-model",
                "input_per_mtok": 1.0,
                "output_per_mtok": 1.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )
    from janus.storage.pricing_db import create_or_update_pricing_override

    await create_or_update_pricing_override(
        db_path,
        {
            "model": "shared-model",
            "input_per_mtok": 42.0,
            "output_per_mtok": 42.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    )

    await reload_pricing(app)

    registry = app.state.pricing_registry
    pricing = registry.get("shared-model")
    assert pricing is not None
    assert pricing.input_per_mtok == 42.0
    assert registry.source_of("shared-model") == "override"
