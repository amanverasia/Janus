from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry


def test_register_and_lookup_provider():
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
    result = registry.lookup("tp/m1")
    assert result is not None
    assert result.prefix == "tp"
    assert result.model == "m1"
    assert result.native_format == "openai"


def test_lookup_unknown_prefix():
    registry = ProviderRegistry()
    assert registry.lookup("no/such") is None


def test_lookup_no_prefix():
    registry = ProviderRegistry()
    assert registry.lookup("modelonly") is None
