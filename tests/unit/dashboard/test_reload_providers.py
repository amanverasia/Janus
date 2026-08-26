from typing import Any

from fastapi import FastAPI

from janus.config.schema import ProviderConfig
from janus.dashboard import reload as reload_module
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.provider_snapshots import (
    acquire_provider_snapshot,
    release_provider_snapshot,
)
from janus.storage.database import init_db


class FakeProvider:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


async def test_reload_attaches_model_sources_and_closes_replaced_same_id(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    row = {
        "id": "gateway-row",
        "catalog_id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": None,
        "models": '["static-model"]',
        "default_model": "live-model",
        "live_models": 1,
        "selected_models": '["custom-model"]',
        "allowed_models": "[]",
        "is_enabled": 1,
    }
    key: dict[str, Any] = {
        "id": "key-one",
        "key_value": "sk-one",
        "custom_base_url": None,
    }

    async def fake_list_providers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [row]

    async def fake_list_keys(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [key]

    async def fake_list_discoveries(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"key-one": ["live-model"]}

    async def fake_list_custom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"provider_id": "gateway-row", "model_id": "custom-model"}]

    new_provider = FakeProvider()
    captured = []

    def fake_build_provider(config):
        captured.append(config)
        return new_provider

    monkeypatch.setattr(reload_module, "list_providers", fake_list_providers)
    monkeypatch.setattr(reload_module, "list_routable_upstream_keys", fake_list_keys)
    monkeypatch.setattr(reload_module, "list_model_ids_for_keys", fake_list_discoveries)
    monkeypatch.setattr(reload_module, "list_custom_models", fake_list_custom)
    monkeypatch.setattr(reload_module, "_build_provider", fake_build_provider)

    app = FastAPI()
    old_provider = FakeProvider()
    app.state.db_path = db_path
    app.state.providers = {"gateway-row::uk_key-one": old_provider}
    app.state.registry = ProviderRegistry()
    app.state.fallback_handler = FallbackHandler(app.state.registry, db_path=db_path)
    snapshot = acquire_provider_snapshot(app)

    await reload_module.reload_providers(app)

    assert len(captured) == 1
    config = captured[0]
    assert config.catalog_id == "openai"
    assert config.live_models is True
    assert config.known_models == ["static-model", "custom-model", "live-model"]
    assert config.visible_models == ["custom-model"]
    assert config.discovered_models == ["live-model"]
    assert app.state.providers == {"gateway-row::uk_key-one": new_provider}
    assert old_provider.close_count == 0
    assert new_provider.close_count == 0
    assert snapshot in app.state.retired_provider_snapshots

    await release_provider_snapshot(app, snapshot)

    assert old_provider.close_count == 1
    assert snapshot not in app.state.retired_provider_snapshots


async def test_reload_reuses_executor_when_only_model_catalog_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    row = {
        "id": "gateway-row",
        "catalog_id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-one",
        "models": '["new-model"]',
        "default_model": "new-model",
        "live_models": 1,
        "selected_models": '["new-model"]',
        "allowed_models": "[]",
        "is_enabled": 1,
    }

    async def fake_list_providers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [row]

    async def no_keys(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def no_discoveries(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {}

    async def no_custom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def unexpected_build(config: ProviderConfig) -> FakeProvider:
        raise AssertionError(f"unexpected rebuild for {config.id}")

    monkeypatch.setattr(reload_module, "list_providers", fake_list_providers)
    monkeypatch.setattr(reload_module, "list_routable_upstream_keys", no_keys)
    monkeypatch.setattr(reload_module, "list_model_ids_for_keys", no_discoveries)
    monkeypatch.setattr(reload_module, "list_custom_models", no_custom)
    monkeypatch.setattr(reload_module, "_build_provider", unexpected_build)

    old_config = ProviderConfig(
        id="gateway-row",
        catalog_id="openai",
        prefix="openai",
        api_type="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key="sk-one",
        models=["old-model"],
        selected_models=["old-model"],
    )
    registry = ProviderRegistry()
    registry.register(old_config)
    old_provider = FakeProvider()
    app = FastAPI()
    app.state.db_path = db_path
    app.state.providers = {"gateway-row": old_provider}
    app.state.registry = registry
    app.state.fallback_handler = FallbackHandler(registry, db_path=db_path)

    await reload_module.reload_providers(app)

    assert app.state.providers == {"gateway-row": old_provider}
    assert old_provider.close_count == 0
    reloaded = app.state.registry.providers["openai"][0]
    assert reloaded.models == ["new-model"]
    assert reloaded.selected_models == ["new-model"]


async def test_reload_uses_canonical_inventory_id_for_gateway_only_prefix(tmp_path, monkeypatch):
    from janus.storage.providers_db import create_provider
    from janus.storage.upstream_keys import create_upstream_key, update_upstream_key

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "kimi-row",
            "catalog_id": "kimi_coding",
            "prefix": "kimi",
            "api_type": "openai_compat",
            "base_url": "https://api.kimi.com/coding/v1",
            "models": ["kimi-model"],
        },
    )
    key = await create_upstream_key(
        db_path,
        provider_id="kimi_coding",
        key_value="kimi-coding-key-1234567890",
    )
    await update_upstream_key(
        db_path,
        str(key["id"]),
        {"status": "active", "is_valid": 1, "is_usable": 1},
    )
    captured: list[ProviderConfig] = []

    def fake_build(config: ProviderConfig) -> FakeProvider:
        captured.append(config)
        return FakeProvider()

    monkeypatch.setattr(reload_module, "_build_provider", fake_build)
    registry = ProviderRegistry()
    app = FastAPI()
    app.state.db_path = db_path
    app.state.providers = {}
    app.state.registry = registry
    app.state.fallback_handler = FallbackHandler(registry, db_path=db_path)

    await reload_module.reload_providers(app)

    assert len(captured) == 1
    assert captured[0].upstream_key_id == key["id"]
    assert captured[0].id == f"kimi-row::uk_{key['id']}"
