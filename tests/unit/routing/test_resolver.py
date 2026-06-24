import pytest

from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.resolver import resolve


def test_resolve_simple():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="glm",
            prefix="glm",
            api_type="openai_compat",
            base_url="https://test.com",
            api_key="sk",
            models=["glm-4.7"],
        )
    )
    result = resolve("glm/glm-4.7", registry)
    assert result is not None
    assert result.model == "glm-4.7"
    assert result.native_format == "openai"


def test_resolve_unknown():
    registry = ProviderRegistry()
    assert resolve("no/model", registry) is None


def test_fallback_handler_single():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="glm",
            prefix="glm",
            api_type="openai_compat",
            base_url="https://test.com",
            api_key="sk",
            models=["glm-4.7"],
        )
    )
    handler = FallbackHandler(registry)
    target = handler.resolve("glm/glm-4.7")
    assert target.model == "glm-4.7"


def test_fallback_handler_unknown_raises():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    with pytest.raises(ValueError, match="Unknown model"):
        handler.resolve("no/such")
