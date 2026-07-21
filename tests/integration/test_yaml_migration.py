import json

import pytest
from cryptography.fernet import Fernet

from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from janus.storage.combos_db import list_combos
from janus.storage.database import init_db
from janus.storage.pricing_db import get_pricing_overrides
from janus.storage.providers_db import list_providers
from janus.storage.settings import get_setting


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_seed_providers_from_config(db, tmp_path):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openai",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://api.openai.com/v1",
                api_key="sk-x",
                models=["gpt-4o"],
            ),
        ],
    )
    await seed_from_config(db, config)
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "openai"
    assert json.loads(providers[0]["models"]) == ["gpt-4o"]


async def test_seed_provider_key_is_encrypted_at_rest(db, tmp_path, monkeypatch):
    from janus.inventory.key_encryption import ENCRYPTED_PREFIX
    from janus.storage.database import get_connection, seed_from_config

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openai",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://api.openai.com/v1",
                api_key="sk-seeded",
                models=["gpt-4o"],
            ),
        ],
    )

    await seed_from_config(db, config)

    async with get_connection(db) as conn:
        async with conn.execute("SELECT api_key FROM providers WHERE id = 'openai'") as cur:
            row = await cur.fetchone()
    assert row["api_key"].startswith(ENCRYPTED_PREFIX)
    assert (await list_providers(db))[0]["api_key"] == "sk-seeded"


async def test_seed_combos_from_config(db, tmp_path):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        combos=[ComboConfig(name="best", models=["openai/gpt-4o"])],
    )
    await seed_from_config(db, config)
    combos = await list_combos(db)
    assert len(combos) == 1
    assert combos[0]["name"] == "best"


async def test_seed_saver_settings(db, tmp_path):
    from janus.storage.database import seed_from_config

    config = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    config.token_savers.rtk.enabled = True
    config.token_savers.caveman.enabled = False
    config.token_savers.caveman.level = "lite"
    config.token_savers.ponytail.enabled = True
    config.token_savers.ponytail.level = "ultra"
    await seed_from_config(db, config)
    assert await get_setting(db, "saver_rtk_enabled") == "true"
    assert await get_setting(db, "saver_caveman_enabled") == "false"
    assert await get_setting(db, "saver_caveman_level") == "lite"
    assert await get_setting(db, "saver_ponytail_enabled") == "true"
    assert await get_setting(db, "saver_ponytail_level") == "ultra"


async def test_seed_pricing(db, tmp_path):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        pricing={"custom-model": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    )
    await seed_from_config(db, config)
    overrides = await get_pricing_overrides(db)
    assert "custom-model" in overrides


async def test_seed_skips_if_data_exists(db, tmp_path):
    from janus.storage.database import seed_from_config
    from janus.storage.providers_db import create_provider

    await create_provider(
        db,
        {
            "id": "existing",
            "prefix": "existing",
            "api_type": "openai_compat",
            "base_url": "https://existing.local",
            "api_key": None,
            "models": [],
        },
    )
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="new",
                prefix="new",
                api_type="openai_compat",
                base_url="https://new.local",
                api_key=None,
                models=[],
            ),
        ],
    )
    await seed_from_config(db, config)
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "existing"


async def test_seed_server_strategy_settings_from_config(db, tmp_path):
    from janus.storage.database import seed_from_config

    config = JanusConfig(
        server=ServerSettings(
            data_dir=tmp_path,
            account_strategy="sticky_rr",
            sticky_limit=7,
            require_api_key=False,
        ),
    )
    await seed_from_config(db, config)
    assert await get_setting(db, "server_account_strategy") == "sticky_rr"
    assert await get_setting(db, "server_sticky_limit") == "7"
    assert await get_setting(db, "server_require_api_key") == "false"
