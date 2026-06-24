from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings


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


def test_combo_config():
    c = ComboConfig(name="my-stack", models=["glm/glm-4.7", "an/claude-sonnet-4-20250514"])
    assert c.name == "my-stack"
    assert len(c.models) == 2


def test_janus_config_has_combos():
    config = JanusConfig(combos=[ComboConfig(name="test", models=["a/b"])])
    assert len(config.combos) == 1
    assert config.combos[0].name == "test"


def test_token_saver_config_defaults():
    from janus.config.schema import TokenSaverConfig

    cfg = TokenSaverConfig()
    assert cfg.rtk.enabled is True
    assert cfg.caveman.enabled is False
    assert cfg.ponytail.enabled is False
    assert cfg.ponytail.level == "full"
