from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.registry import ProviderRegistry


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
