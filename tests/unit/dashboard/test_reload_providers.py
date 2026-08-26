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


def _gateway_row(
    provider_id: str,
    *,
    base_url: str,
    api_key: str | None = None,
    quota_limit: int | None = None,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "catalog_id": "openai",
        "prefix": "shared",
        "api_type": "openai_compat",
        "base_url": base_url,
        "api_key": api_key,
        "models": '["model"]',
        "default_model": "model",
        "live_models": 1,
        "selected_models": "[]",
        "allowed_models": "[]",
        "is_enabled": 1,
        "quota_window": "daily" if quota_limit is not None else None,
        "quota_limit": quota_limit,
        "quota_metric": "requests",
    }


def _inventory_key(
    key_id: str,
    value: str,
    *,
    source_node: str | None = None,
    custom_base_url: str | None = None,
    rate_limit_rpm: int | None = None,
) -> dict[str, Any]:
    return {
        "id": key_id,
        "key_value": value,
        "source_node": source_node,
        "custom_base_url": custom_base_url,
        "rate_limit_rpm": rate_limit_rpm,
        "rate_limit_rpd": None,
    }


async def _capture_reload(
    tmp_path: Any,
    monkeypatch: Any,
    rows: list[dict[str, Any]],
    keys: list[dict[str, Any]],
) -> list[ProviderConfig]:
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    async def fake_list_providers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return rows

    async def fake_list_keys(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return keys

    async def no_discoveries(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {}

    async def no_custom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    captured: list[ProviderConfig] = []

    def fake_build(config: ProviderConfig) -> FakeProvider:
        captured.append(config)
        return FakeProvider()

    monkeypatch.setattr(reload_module, "list_providers", fake_list_providers)
    monkeypatch.setattr(reload_module, "list_routable_upstream_keys", fake_list_keys)
    monkeypatch.setattr(reload_module, "list_model_ids_for_keys", no_discoveries)
    monkeypatch.setattr(reload_module, "list_custom_models", no_custom)
    monkeypatch.setattr(reload_module, "_build_provider", fake_build)

    registry = ProviderRegistry()
    app = FastAPI()
    app.state.db_path = db_path
    app.state.providers = {}
    app.state.registry = registry
    app.state.fallback_handler = FallbackHandler(registry, db_path=db_path)

    await reload_module.reload_providers(app)
    return captured


async def test_reload_partitions_mirrors_between_same_prefix_rows(tmp_path, monkeypatch):
    rows = [
        _gateway_row("account-a", base_url="https://a.example/v1", quota_limit=10),
        _gateway_row("account-b", base_url="https://b.example/v1", quota_limit=20),
    ]
    keys = [
        _inventory_key(
            "key-a",
            "secret-a",
            source_node="gateway:account-a",
            rate_limit_rpm=100,
        ),
        _inventory_key(
            "key-b",
            "secret-b",
            source_node="gateway:account-b",
            rate_limit_rpm=200,
        ),
    ]

    captured = await _capture_reload(tmp_path, monkeypatch, rows, keys)

    assert len(captured) == 2
    configs = {config.upstream_key_id: config for config in captured}
    assert configs["key-a"].id == "account-a::uk_key-a"
    assert configs["key-a"].base_url == "https://a.example/v1"
    assert configs["key-a"].quota_limit == 10
    assert configs["key-a"].rate_limit_rpm == 100
    assert configs["key-b"].id == "account-b::uk_key-b"
    assert configs["key-b"].base_url == "https://b.example/v1"
    assert configs["key-b"].quota_limit == 20
    assert configs["key-b"].rate_limit_rpm == 200


async def test_reload_assigns_unbound_manual_key_once_to_logical_primary(tmp_path, monkeypatch):
    rows = [
        _gateway_row("account-b", base_url="https://b.example/v1"),
        _gateway_row("account-a", base_url="https://a.example/v1"),
    ]
    keys = [_inventory_key("manual", "manual-secret")]

    captured = await _capture_reload(tmp_path, monkeypatch, rows, keys)

    routed = [config for config in captured if config.upstream_key_id == "manual"]
    assert len(routed) == 1
    assert routed[0].id == "account-a::uk_manual"


async def test_reload_binds_unique_custom_base_url_to_matching_row(tmp_path, monkeypatch):
    rows = [
        _gateway_row("account-a", base_url="https://a.example/v1"),
        _gateway_row("account-b", base_url="https://b.example/v1/"),
    ]
    keys = [
        _inventory_key(
            "regional",
            "regional-secret",
            custom_base_url="https://b.example/v1",
        )
    ]

    captured = await _capture_reload(tmp_path, monkeypatch, rows, keys)

    routed = [config for config in captured if config.upstream_key_id == "regional"]
    assert len(routed) == 1
    assert routed[0].id == "account-b::uk_regional"
    assert routed[0].base_url == "https://b.example/v1"


async def test_reload_excludes_mirror_for_disabled_or_missing_gateway_row(tmp_path, monkeypatch):
    rows = [
        _gateway_row(
            "enabled-account",
            base_url="https://enabled.example/v1",
            api_key="enabled-static",
        )
    ]
    keys = [
        _inventory_key(
            "disabled-mirror",
            "disabled-secret",
            source_node="gateway:disabled-account",
            custom_base_url="https://enabled.example/v1",
        )
    ]

    captured = await _capture_reload(tmp_path, monkeypatch, rows, keys)

    assert len(captured) == 1
    assert captured[0].id == "enabled-account"
    assert captured[0].api_key == "enabled-static"
    assert captured[0].upstream_key_id is None


async def test_reload_deduplicates_inventory_values_and_static_fallback(tmp_path, monkeypatch):
    rows = [
        _gateway_row("account-a", base_url="https://a.example/v1"),
        _gateway_row(
            "account-b",
            base_url="https://b.example/v1",
            api_key="same-secret",
        ),
    ]
    keys = [
        _inventory_key("manual-first", "same-secret"),
        _inventory_key("manual-duplicate", "same-secret"),
    ]

    captured = await _capture_reload(tmp_path, monkeypatch, rows, keys)

    assert len(captured) == 1
    assert captured[0].id == "account-a::uk_manual-first"
    assert captured[0].api_key == "same-secret"


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


async def test_reload_shares_custom_models_with_enabled_sibling_account(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    row = {
        "id": "account-b",
        "catalog_id": "custom",
        "prefix": "shared",
        "api_type": "openai_compat",
        "base_url": "https://example.test/v1",
        "api_key": "sk-b",
        "models": "[]",
        "default_model": None,
        "live_models": 1,
        "selected_models": "[]",
        "allowed_models": "[]",
        "is_enabled": 1,
    }

    async def fake_list_providers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [row]

    async def no_keys(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def no_discoveries(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {}

    async def anchored_custom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "provider_id": "disabled-account-a",
                "provider_prefix": "shared",
                "model_id": "custom-model",
            }
        ]

    built: list[ProviderConfig] = []

    def fake_build_provider(config: ProviderConfig) -> FakeProvider:
        built.append(config)
        return FakeProvider()

    monkeypatch.setattr(reload_module, "list_providers", fake_list_providers)
    monkeypatch.setattr(reload_module, "list_routable_upstream_keys", no_keys)
    monkeypatch.setattr(reload_module, "list_model_ids_for_keys", no_discoveries)
    monkeypatch.setattr(reload_module, "list_custom_models", anchored_custom)
    monkeypatch.setattr(reload_module, "_build_provider", fake_build_provider)

    app = FastAPI()
    app.state.db_path = db_path
    app.state.providers = {}
    app.state.registry = ProviderRegistry()
    app.state.fallback_handler = FallbackHandler(app.state.registry, db_path=db_path)

    await reload_module.reload_providers(app)

    assert len(built) == 1
    assert built[0].custom_models == ["custom-model"]
    assert app.state.registry.lookup("shared/custom-model")


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
