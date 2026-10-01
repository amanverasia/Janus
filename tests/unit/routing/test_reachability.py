from typing import Any

from janus.catalog import PROVIDERS
from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.reachability import (
    KnownModel,
    UnreachableReason,
    classify,
    collect_known_models,
    connect_target,
    cooled_down,
)


def _config(
    prefix: str,
    models: list[str],
    *,
    config_id: str | None = None,
    upstream_key_id: str | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        id=config_id or prefix,
        prefix=prefix,
        api_type="openai_compat",
        base_url=f"https://{prefix}.example/v1",
        models=models,
        discovered_models=list(models),
        upstream_key_id=upstream_key_id,
    )


def _registry(*configs: ProviderConfig) -> ProviderRegistry:
    registry = ProviderRegistry()
    for config in configs:
        registry.register(config)
    return registry


def _row(prefix: str, *, enabled: int = 1, models: Any = None, **extra: Any) -> dict[str, Any]:
    return {"id": prefix, "prefix": prefix, "is_enabled": enabled, "models": models, **extra}


def _by_key(report_items: list[Any]) -> dict[tuple[str, str], Any]:
    return {(item.prefix, item.model): item for item in report_items}


def _anthropic_defaults() -> list[str]:
    return list(PROVIDERS["anthropic"]["gateway"]["default_models"])


def test_catalog_defaults_unreachable_without_provider() -> None:
    known = collect_known_models([], [])
    report = classify(known, ProviderRegistry(), [])
    unreachable = _by_key(report.unreachable)
    defaults = _anthropic_defaults()
    assert defaults
    for model in defaults:
        item = unreachable[("anthropic", model)]
        assert item.reason is UnreachableReason.NO_PROVIDER
        assert item.catalog_id == "anthropic"
        assert item.source == "catalog"
    assert report.reachable == []


def test_reachable_model_excluded() -> None:
    registry = _registry(_config("openai", ["gpt-4o"]))
    rows = [_row("openai", models='["gpt-4o"]')]
    report = classify(collect_known_models(rows, []), registry, rows)
    assert ("openai", "gpt-4o") in _by_key(report.reachable)
    assert ("openai", "gpt-4o") not in _by_key(report.unreachable)


def test_provider_disabled_reason() -> None:
    rows = [_row("anthropic", enabled=0)]
    report = classify(collect_known_models(rows, []), ProviderRegistry(), rows)
    unreachable = _by_key(report.unreachable)
    for model in _anthropic_defaults():
        assert unreachable[("anthropic", model)].reason is UnreachableReason.PROVIDER_DISABLED


def test_no_active_credential_reason() -> None:
    rows = [_row("anthropic")]
    report = classify(collect_known_models(rows, []), ProviderRegistry(), rows)
    unreachable = _by_key(report.unreachable)
    for model in _anthropic_defaults():
        assert unreachable[("anthropic", model)].reason is UnreachableReason.NO_ACTIVE_CREDENTIAL


def test_model_not_enabled_reason() -> None:
    registry = _registry(_config("openai", ["gpt-4o"]))
    rows = [_row("openai", models='["gpt-4o"]')]
    known = [
        KnownModel(model="gpt-4o", prefix="openai", catalog_id="openai", source="catalog"),
        KnownModel(model="o3", prefix="openai", catalog_id="openai", source="catalog"),
    ]
    report = classify(known, registry, rows)
    assert [(item.model, item.reason) for item in report.unreachable] == [
        ("o3", UnreachableReason.MODEL_NOT_ENABLED)
    ]
    assert [item.model for item in report.reachable] == ["gpt-4o"]


def test_mixed_enabled_disabled_rows() -> None:
    rows = [
        _row("anthropic", enabled=0),
        {"id": "anthropic-2", "prefix": "anthropic", "is_enabled": 1, "models": None},
    ]
    report = classify(collect_known_models(rows, []), ProviderRegistry(), rows)
    unreachable = _by_key(report.unreachable)
    for model in _anthropic_defaults():
        assert unreachable[("anthropic", model)].reason is UnreachableReason.NO_ACTIVE_CREDENTIAL


def test_dedupe_prefers_catalog_source() -> None:
    assert "gpt-4o" in PROVIDERS["openai"]["gateway"]["default_models"]
    known = collect_known_models([], [{"provider_id": "openai", "model_id": "gpt-4o"}])
    matches = [k for k in known if (k.prefix, k.model) == ("openai", "gpt-4o")]
    assert matches == [
        KnownModel(model="gpt-4o", prefix="openai", catalog_id="openai", source="catalog")
    ]


def test_discovered_model_maps_inventory_id_to_prefix() -> None:
    known = collect_known_models([], [{"provider_id": "google", "model_id": "gemini-x"}])
    matches = [k for k in known if k.model == "gemini-x"]
    assert matches == [
        KnownModel(model="gemini-x", prefix="gemini", catalog_id="google", source="discovered")
    ]


def test_discovered_without_gateway_skipped() -> None:
    known = collect_known_models([], [{"provider_id": "replicate", "model_id": "rep-model"}])
    assert [k for k in known if k.model == "rep-model"] == []


def test_configured_models_and_namespaced_strip() -> None:
    rows = [_row("acme", models='["acme/m1", "m2"]')]
    known = collect_known_models(rows, [])
    assert [k for k in known if k.prefix == "acme"] == [
        KnownModel(model="m1", prefix="acme", catalog_id="acme", source="configured"),
        KnownModel(model="m2", prefix="acme", catalog_id="acme", source="configured"),
    ]


def test_configured_list_models_and_catalog_id_resolution() -> None:
    rows = [
        _row("gemini", models=["gemini-custom"]),
        _row("acme", models=["x"], catalog_id="openai"),
    ]
    known = _by_key(collect_known_models(rows, []))
    assert known[("gemini", "gemini-custom")].catalog_id == "google"
    assert known[("acme", "x")].catalog_id == "openai"


def test_custom_and_empty_prefix_skipped() -> None:
    assert PROVIDERS["custom"]["gateway"]["prefix"] == ""
    rows = [_row("", models='["m"]')]
    known = collect_known_models(rows, [{"provider_id": "custom", "model_id": "m"}])
    assert [k for k in known if k.prefix == "" or k.catalog_id == "custom"] == []


def test_provider_without_models_produces_nothing() -> None:
    assert PROVIDERS["openrouter"]["gateway"]["default_models"] == []
    known = collect_known_models([], [])
    assert [k for k in known if k.prefix == "openrouter"] == []
    report = classify(known, ProviderRegistry(), [])
    assert [u for u in report.unreachable if u.prefix == "openrouter"] == []


def test_known_models_sorted() -> None:
    known = collect_known_models([], [])
    keys = [(k.prefix, k.model) for k in known]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def _two_account_openai() -> tuple[ProviderRegistry, FallbackHandler, list[KnownModel]]:
    registry = _registry(
        _config("openai", ["gpt-4o"], config_id="openai::a", upstream_key_id="key-a"),
        _config("openai", ["gpt-4o"], config_id="openai::b", upstream_key_id="key-b"),
    )
    handler = FallbackHandler(registry, db_path=None)
    reachable = [KnownModel(model="gpt-4o", prefix="openai", catalog_id="openai", source="catalog")]
    return registry, handler, reachable


def test_cooled_down_all_accounts() -> None:
    registry, handler, reachable = _two_account_openai()
    handler.mark_cooldown("key-a", "rate_limit", model="gpt-4o")
    handler.mark_cooldown("key-b", "rate_limit", model="gpt-4o")
    assert cooled_down(reachable, registry, handler) == reachable


def test_cooled_down_one_available_not_soon() -> None:
    registry, handler, reachable = _two_account_openai()
    handler.mark_cooldown("key-a", "rate_limit", model="gpt-4o")
    assert cooled_down(reachable, registry, handler) == []


def test_cooled_down_all_scope() -> None:
    registry, handler, reachable = _two_account_openai()
    handler.mark_cooldown("key-a", "auth_error")
    handler.mark_cooldown("key-b", "rate_limit", model="gpt-4o")
    assert cooled_down(reachable, registry, handler) == reachable


def test_cooled_down_uses_config_id_without_upstream_key() -> None:
    registry = _registry(_config("acme", ["m1"]))
    handler = FallbackHandler(registry, db_path=None)
    reachable = [KnownModel(model="m1", prefix="acme", catalog_id="acme", source="configured")]
    handler.mark_cooldown("acme", "rate_limit", model="m1")
    assert cooled_down(reachable, registry, handler) == reachable


def test_cooled_down_respects_limit() -> None:
    registry = _registry(_config("acme", ["m1", "m2", "m3"]))
    handler = FallbackHandler(registry, db_path=None)
    handler.mark_cooldown("acme", "auth_error")
    reachable = [
        KnownModel(model=m, prefix="acme", catalog_id="acme", source="configured")
        for m in ("m1", "m2", "m3")
    ]
    assert cooled_down(reachable, registry, handler, limit=2) == reachable[:2]


def test_connect_target() -> None:
    assert connect_target("anthropic") == {
        "kind": "connect",
        "href": "/dashboard/ui/connect?provider=anthropic",
    }
    assert "inventory" not in PROVIDERS["cline"]
    assert connect_target("cline") == {"kind": "providers", "href": "/dashboard/ui/providers"}
    assert connect_target("not-a-provider") == {
        "kind": "providers",
        "href": "/dashboard/ui/providers",
    }
