from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.registry import ProviderRegistry, model_allowed


def test_register_and_lookup_single():
    registry = ProviderRegistry()
    config = ProviderConfig(
        id="test",
        prefix="tp",
        api_type="openai_compat",
        base_url="https://test.com/v1",
        api_key="sk-test",
        models=["m1"],
    )
    registry.register(config)
    targets = registry.lookup("tp/m1")
    assert targets is not None
    assert len(targets) == 1
    assert targets[0].model == "m1"
    assert targets[0].native_format == "openai"
    assert targets[0].account_id == "test"


def test_multi_account_same_prefix():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="ds-1",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k1",
            models=["m1"],
        )
    )
    registry.register(
        ProviderConfig(
            id="ds-2",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k2",
            models=["m1"],
        )
    )
    targets = registry.lookup("ds/m1")
    assert targets is not None
    assert len(targets) == 2
    assert targets[0].account_id == "ds-1"
    assert targets[1].account_id == "ds-2"


def test_upstream_key_id_used_as_account_id():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openai-main::uk_key-1",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            api_key="sk-live",
            models=["gpt-4o"],
            upstream_key_id="key-1",
        )
    )
    targets = registry.lookup("openai/gpt-4o")
    assert targets is not None
    assert targets[0].account_id == "key-1"
    assert targets[0].provider_config.id == "openai-main::uk_key-1"


def test_lookup_returns_none_for_unknown():
    registry = ProviderRegistry()
    assert registry.lookup("no/such") is None


def test_lookup_no_prefix():
    registry = ProviderRegistry()
    assert registry.lookup("modelonly") is None


def test_register_combo():
    registry = ProviderRegistry()
    registry.register_combo(ComboConfig(name="stack", models=["a/b", "c/d"]))
    result = registry.lookup_combo("stack")
    assert result == ["a/b", "c/d"]


def test_lookup_combo_unknown():
    registry = ProviderRegistry()
    assert registry.lookup_combo("nope") is None


def test_model_allowed_empty_allowlist_routes_anything():
    assert model_allowed("anything-goes", []) is True


def test_model_allowed_exact_match_blocks_others():
    assert model_allowed("claude-opus-4-7", ["claude-opus-4-7"]) is True
    assert model_allowed("claude-sonnet-4-5", ["claude-opus-4-7"]) is False


def test_model_allowed_glob_matches():
    assert model_allowed("claude-opus-4-7", ["claude-opus-*"]) is True
    assert model_allowed("claude-sonnet-4-5", ["claude-opus-*"]) is False


def test_lookup_filters_out_disallowed_model():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="anthropic",
            prefix="an",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-test",
            models=["claude-opus-4-7", "claude-sonnet-4-5"],
            allowed_models=["claude-opus-4-7"],
        )
    )
    assert registry.lookup("an/claude-opus-4-7") is not None
    assert registry.lookup("an/claude-sonnet-4-5") is None


def test_lookup_glob_allowlist():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="anthropic",
            prefix="an",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-test",
            models=["claude-opus-4-7", "claude-sonnet-4-5"],
            allowed_models=["claude-opus-*"],
        )
    )
    assert registry.lookup("an/claude-opus-4-7") is not None
    assert registry.lookup("an/claude-sonnet-4-5") is None


def test_lookup_multi_account_different_allowlists():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="an-1",
            prefix="an",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="k1",
            models=["claude-opus-4-7"],
            allowed_models=["claude-opus-4-7"],
        )
    )
    registry.register(
        ProviderConfig(
            id="an-2",
            prefix="an",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="k2",
            models=["claude-opus-4-7"],
            allowed_models=["claude-sonnet-4-5"],
        )
    )
    targets = registry.lookup("an/claude-opus-4-7")
    assert targets is not None
    assert len(targets) == 1
    assert targets[0].account_id == "an-1"


def test_direct_lookup_ignores_catalog_selection_but_filters_discovered_accounts():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openai::uk_one",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            models=["gpt-4o"],
            selected_models=["gpt-4o"],
            discovered_models=["gpt-4.1"],
            upstream_key_id="one",
        )
    )
    registry.register(
        ProviderConfig(
            id="openai::uk_two",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            models=["gpt-4o"],
            selected_models=["gpt-4o"],
            discovered_models=None,
            upstream_key_id="two",
        )
    )

    targets = registry.lookup("openai/unlisted-direct-model")

    assert targets is not None
    assert [target.account_id for target in targets] == ["two"]


def test_empty_discovery_is_authoritative():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openai",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            discovered_models=[],
        )
    )
    assert registry.lookup("openai/any-model") is None


def test_custom_models_survive_nonempty_discovery():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openai",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            models=["configured"],
            custom_models=["custom"],
            discovered_models=["live"],
        )
    )
    assert registry.lookup("openai/configured") is None
    assert registry.lookup("openai/custom") is not None
    assert registry.lookup("openai/live") is not None
    assert registry.lookup("openai/unknown") is None


def test_bare_lookup_prefers_exact_default_model():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="default",
            prefix="first",
            api_type="anthropic",
            base_url="https://first.example",
            default_model="shared-model",
        )
    )
    registry.register(
        ProviderConfig(
            id="known",
            prefix="second",
            api_type="openai_compat",
            base_url="https://second.example/v1",
            models=["shared-model"],
        )
    )

    targets = registry.lookup("shared-model")

    assert targets is not None
    assert [target.provider_config.id for target in targets] == ["default"]
    assert targets[0].prefix == "first"
    assert targets[0].native_format == "anthropic"


def test_bare_provider_prefix_resolves_its_default_model():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openai",
            prefix="openai",
            api_type="openai_compat",
            base_url="https://api.openai.com/v1",
            default_model="openai/gpt-4.1",
            discovered_models=["gpt-4.1"],
        )
    )

    targets = registry.lookup("openai")

    assert targets is not None
    assert targets[0].prefix == "openai"
    assert targets[0].model == "gpt-4.1"


def test_bare_lookup_falls_back_to_static_custom_and_discovered_known_models():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="configured",
            prefix="provider",
            api_type="openai_compat",
            base_url="https://provider.example/v1",
            models=["static"],
            custom_models=["custom"],
            selected_models=["static"],
        )
    )
    registry.register(
        ProviderConfig(
            id="discovered",
            prefix="provider",
            api_type="openai_compat",
            base_url="https://provider.example/v1",
            discovered_models=["live"],
        )
    )

    assert registry.lookup("static") is not None
    assert registry.lookup("live") is not None
    assert registry.lookup("custom") is not None
    assert registry.lookup("unknown") is None


def test_bare_native_model_with_slash_falls_back_to_known_scan():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="openrouter",
            prefix="openrouter",
            api_type="openai_compat",
            base_url="https://openrouter.ai/api/v1",
            models=["openai/gpt-4o"],
        )
    )

    targets = registry.lookup("openai/gpt-4o")

    assert targets is not None
    assert targets[0].prefix == "openrouter"
    assert targets[0].model == "openai/gpt-4o"


def test_bare_lookup_respects_account_discovery_entitlement():
    registry = ProviderRegistry()
    for account, discovered in (("one", ["other"]), ("two", ["known"])):
        registry.register(
            ProviderConfig(
                id=f"provider::{account}",
                prefix="provider",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                models=["known"],
                discovered_models=discovered,
                upstream_key_id=account,
            )
        )

    targets = registry.lookup("known")

    assert targets is not None
    assert [target.account_id for target in targets] == ["two"]


def test_has_route_matches_lookup_across_resolution_paths():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="bounded",
            prefix="bounded",
            api_type="openai_compat",
            base_url="https://bounded.example/v1",
            models=["configured", "native/vendor-model"],
            custom_models=["custom"],
            discovered_models=["live", "native/vendor-model"],
            default_model="live",
        )
    )
    registry.register(
        ProviderConfig(
            id="unrestricted",
            prefix="openrouter",
            api_type="openai_compat",
            base_url="https://openrouter.example/v1",
            models=["vendor/model"],
            allowed_models=["vendor/*"],
        )
    )
    registry.register(
        ProviderConfig(
            id="alias",
            prefix="xiaomi",
            api_type="openai_compat",
            base_url="https://xiaomi.example/v1",
            discovered_models=["mimo-v2.5"],
        )
    )

    names = [
        "bounded/live",
        "bounded/configured",
        "bounded/custom",
        "bounded/unknown",
        "bounded",
        "live",
        "custom",
        "native/vendor-model",
        "openrouter/vendor/other",
        "openrouter/not-allowed",
        "mimo/mimo-v2.5",
        "missing/model",
    ]

    assert {name: registry.has_route(name) for name in names} == {
        name: registry.lookup(name) is not None for name in names
    }


def test_has_route_uses_precomputed_high_cardinality_indexes(monkeypatch):
    registry = ProviderRegistry()
    for account in range(64):
        registry.register(
            ProviderConfig(
                id=f"bulk-{account}",
                prefix="bulk",
                api_type="openai_compat",
                base_url="https://bulk.example/v1",
                models=[f"model-{index}" for index in range(128)],
                discovered_models=[f"model-{index}" for index in range(128)],
            )
        )

    def fail_materialization(*args, **kwargs):
        raise AssertionError("has_route must not build or scan resolved targets")

    monkeypatch.setattr("janus.providers.registry._account_supports", fail_materialization)
    monkeypatch.setattr("janus.providers.registry._native_format", fail_materialization)

    assert registry.has_route("bulk/model-127") is True
    assert registry.has_route("model-127") is True
    assert registry.has_route("bulk/missing") is False
    assert registry.has_route("missing") is False
