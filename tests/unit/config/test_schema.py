from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings


def test_server_defaults():
    s = ServerSettings()
    assert s.port == 20128
    assert s.host == "127.0.0.1"
    assert s.require_api_key is False


def test_provider_config_validation():
    p = ProviderConfig(
        id="glm",
        prefix="glm",
        api_type="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="test-key",
        models=["glm-4.7"],
    )
    assert p.id == "glm"
    assert p.models == ["glm-4.7"]


def test_full_config():
    config = JanusConfig(
        server=ServerSettings(port=8080),
        providers=[
            ProviderConfig(
                id="an",
                prefix="an",
                api_type="anthropic",
                base_url="https://api.anthropic.com",
                api_key="sk-test",
                models=["claude-sonnet-4-20250514"],
            )
        ],
    )
    assert config.server.port == 8080
    assert len(config.providers) == 1
