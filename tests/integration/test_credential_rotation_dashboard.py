from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from tests.fixtures.dashboard_auth import with_dashboard_auth


async def _seed(app) -> None:
    from janus.storage.database import init_db, seed_from_config

    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="rotated",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="sk-provider-original",
                models=["model-1"],
            )
        ],
        combos=[ComboConfig(name="combo", models=["openai/model-1"])],
    )
    app = create_app(config=config)
    return with_dashboard_auth(app)


@pytest.mark.asyncio
async def test_rotated_key_without_previous_is_skipped_and_surfaced(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from janus.dashboard.reload import reload_providers
    from janus.inventory.key_encryption import encrypt_key_value
    from janus.storage.database import get_connection
    from janus.storage.upstream_keys import create_upstream_key

    await _seed(app)
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", old_key)
    record = await create_upstream_key(
        app.state.db_path, provider_id="openai", key_value="sk-upstream-rotated-away"
    )
    async with get_connection(app.state.db_path) as db:
        await db.execute(
            "UPDATE providers SET api_key = ? WHERE id = ?",
            (encrypt_key_value("sk-provider-original"), "rotated"),
        )
        await db.commit()

    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", new_key)
    monkeypatch.delenv("INVENTORY_ENCRYPTION_PREVIOUS_KEY", raising=False)
    await asyncio.wait_for(reload_providers(app), timeout=10)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        keys_state = await client.get(
            "/dashboard/api/v2/state/inventory-keys",
        )
        assert keys_state.status_code == 200
        keys = keys_state.json()["data"]["keys"]
        row = next(item for item in keys if item["id"] == record["id"])
        assert row["decryptable"] is False

        providers_state = await client.get(
            "/dashboard/api/v2/state/providers",
        )
        assert providers_state.status_code == 200
        provider_row = next(
            item for item in providers_state.json()["data"]["providers"] if item["id"] == "rotated"
        )
        assert provider_row["decryptable"] is False

        overview_state = await client.get(
            "/dashboard/api/v2/state/overview",
        )
        assert overview_state.status_code == 200
        alerts = overview_state.json()["alerts"]
        alert = next(item for item in alerts if item["id"] == "credentials:undecryptable")
        assert "2" in alert["detail"]
        assert "INVENTORY_ENCRYPTION_PREVIOUS_KEY" in alert["detail"]

        reveal = await client.post(
            f"/dashboard/api/inventory/keys/{record['id']}/reveal",
        )
        assert reveal.status_code == 503


async def _no_pricing_sync(_app) -> bool:
    return False


@pytest.mark.asyncio
async def test_startup_guard_refuses_plaintext_credentials_without_opt_in(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from janus.inventory.key_encryption import CredentialEncryptionError

    monkeypatch.delenv("INVENTORY_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JANUS_ALLOW_INSECURE_DEV_KEY", raising=False)
    monkeypatch.setattr("janus.app._pricing_catalog_needs_sync", _no_pricing_sync)
    monkeypatch.setattr("janus.inventory.scheduler.scheduler_enabled", lambda: False)
    monkeypatch.setattr("janus.pricing.scheduler.pricing_scheduler_enabled", lambda: False)
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="plain",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="sk-plaintext-real",
                models=["model-1"],
            )
        ],
    )
    app = with_dashboard_auth(create_app(config=config))
    with pytest.raises(CredentialEncryptionError, match="JANUS_ALLOW_INSECURE_DEV_KEY=1"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_startup_seals_plaintext_once_key_configured(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet as FernetClass

    from janus.inventory.key_encryption import ENCRYPTED_PREFIX
    from janus.storage.database import get_connection

    key = FernetClass.generate_key().decode()
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", key)
    monkeypatch.setattr("janus.app._pricing_catalog_needs_sync", _no_pricing_sync)
    monkeypatch.setattr("janus.inventory.scheduler.scheduler_enabled", lambda: False)
    monkeypatch.setattr("janus.pricing.scheduler.pricing_scheduler_enabled", lambda: False)
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="sealme",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="sk-plaintext-real",
                models=["model-1"],
            )
        ],
    )
    app = with_dashboard_auth(create_app(config=config))
    async with app.router.lifespan_context(app):
        async with get_connection(app.state.db_path) as db:
            async with db.execute("SELECT api_key FROM providers WHERE id = 'sealme'") as cur:
                row = await cur.fetchone()
    assert isinstance(row[0], str)
    assert row[0].startswith(ENCRYPTED_PREFIX)
