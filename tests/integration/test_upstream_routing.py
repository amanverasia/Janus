import pytest

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.routing.reload_bridge import bind_reload_app
from janus.storage.database import init_db, seed_from_config
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


@pytest.mark.asyncio
async def test_reload_providers_prefers_upstream_inventory_keys(tmp_path):
    provider = ProviderConfig(
        id="openai-main",
        prefix="openai",
        api_type="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key="static-fallback",
        models=["gpt-4o"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider],
    )
    app = create_app(config=cfg)
    bind_reload_app(app)
    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, cfg)

    record = await create_upstream_key(
        app.state.db_path,
        provider_id="openai",
        key_value="sk-inventory-live",
    )
    await update_upstream_key(
        app.state.db_path,
        record["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1},
    )

    await reload_providers(app)

    provider_id = f"openai-main::uk_{record['id']}"
    assert provider_id in app.state.providers
    live_provider = app.state.providers[provider_id]
    assert live_provider.api_key == "sk-inventory-live"

    targets = app.state.registry.lookup("openai/gpt-4o")
    assert targets is not None
    assert len(targets) == 1
    assert targets[0].account_id == record["id"]

    await live_provider.close()
