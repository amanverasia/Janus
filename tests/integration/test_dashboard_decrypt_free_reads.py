from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.alerts import invalidate_dashboard_alerts
from janus.dashboard.api_v2 import _SECTIONS
from janus.dashboard.reload import reload_providers
from janus.storage import providers_db, upstream_keys
from tests.fixtures.dashboard_auth import with_dashboard_auth


async def test_dashboard_reads_never_decrypt_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = with_dashboard_auth(
        create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/dashboard/api/v2/state/inventory")).status_code == 200
        await providers_db.create_provider(
            app.state.db_path,
            {
                "id": "openai",
                "prefix": "openai",
                "api_type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "api_key": "synthetic-static-credential",
                "models": ["gpt-4o"],
            },
        )
        key = await upstream_keys.create_upstream_key(
            app.state.db_path, provider_id="openai", key_value="synthetic-inventory-credential"
        )
        await upstream_keys.update_upstream_key(
            app.state.db_path, key["id"], {"status": "active", "is_valid": 1, "is_usable": 1}
        )
        await reload_providers(app)
        decrypt = Mock(side_effect=AssertionError("Dashboard read attempted credential decryption"))
        monkeypatch.setattr(providers_db, "decrypt_key_value", decrypt)
        monkeypatch.setattr(upstream_keys, "decrypt_key_value", decrypt)
        paths = [f"/dashboard/api/v2/state/{section}" for section in _SECTIONS]
        paths.extend(
            [
                "/dashboard/api/v2/health",
                "/dashboard/api/inventory/keys",
                f"/dashboard/api/inventory/keys/{key['id']}",
            ]
        )
        for path in paths:
            invalidate_dashboard_alerts(app)
            response = await client.get(path)
            assert response.status_code == 200, path
            assert decrypt.call_count == 0, path
            assert "synthetic-static-credential" not in response.text, path
            assert "synthetic-inventory-credential" not in response.text, path
        from janus.inventory.migrate import verify_inventory

        verification = await verify_inventory(app.state.db_path)
        assert verification["total"] == 1
        assert decrypt.call_count == 0


@pytest.mark.parametrize("legacy", [False, True])
async def test_reload_decrypts_only_selected_inventory_credentials(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = with_dashboard_auth(
        create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/inventory")
    await providers_db.create_provider(
        app.state.db_path,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o"],
        },
    )
    for priority in (1, 2):
        key = await upstream_keys.create_upstream_key(
            app.state.db_path,
            provider_id="openai",
            key_value="synthetic-duplicate-credential",
            priority=priority,
        )
        await upstream_keys.update_upstream_key(
            app.state.db_path, key["id"], {"status": "active", "is_valid": 1, "is_usable": 1}
        )
    if legacy:
        from janus.storage.database import get_connection

        async with get_connection(app.state.db_path) as db:
            await db.execute("UPDATE upstream_keys SET key_hash = NULL")
            await db.commit()
    decrypt = Mock(wraps=upstream_keys.decrypt_key_value)
    monkeypatch.setattr(upstream_keys, "decrypt_key_value", decrypt)
    await reload_providers(app)
    assert decrypt.call_count == (2 if legacy else 1)
    targets = app.state.registry.lookup("openai/gpt-4o")
    assert len(targets) == 1
    assert targets[0].provider_config.api_key == "synthetic-duplicate-credential"
