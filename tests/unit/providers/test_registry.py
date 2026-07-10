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
