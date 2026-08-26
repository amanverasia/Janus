from janus.routing.upstream_expand import expand_gateway_provider


def test_expand_gateway_provider_uses_upstream_keys():
    row = {
        "id": "openai-main",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "static-key",
        "models": '["gpt-4o"]',
    }
    upstream_keys = [
        {"id": "uk-1", "key_value": "sk-one", "rate_limit_rpm": 500, "rate_limit_rpd": 10000},
        {"id": "uk-2", "key_value": "sk-two", "custom_base_url": "https://proxy.example/v1"},
    ]
    configs = expand_gateway_provider(row, upstream_keys)
    assert len(configs) == 2
    assert configs[0].id == "openai-main::uk_uk-1"
    assert configs[0].api_key == "sk-one"
    assert configs[0].upstream_key_id == "uk-1"
    assert configs[0].rate_limit_rpm == 500
    assert configs[0].rate_limit_rpd == 10000
    assert configs[1].base_url == "https://proxy.example/v1"
    assert configs[1].rate_limit_rpm is None


def test_expand_gateway_provider_falls_back_to_static_key():
    row = {
        "id": "openai-main",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "static-key",
        "models": "[]",
    }
    configs = expand_gateway_provider(row, [])
    assert len(configs) == 1
    assert configs[0].id == "openai-main"
    assert configs[0].api_key == "static-key"
    assert configs[0].upstream_key_id is None


def test_expand_gateway_provider_parses_allowed_models_static_key():
    row = {
        "id": "an-main",
        "prefix": "an",
        "api_type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "static-key",
        "models": '["claude-opus-4-7", "claude-sonnet-4-5"]',
        "allowed_models": '["claude-opus-4-7"]',
    }
    configs = expand_gateway_provider(row, [])
    assert len(configs) == 1
    assert configs[0].allowed_models == ["claude-opus-4-7"]


def test_expand_gateway_provider_parses_allowed_models_with_upstream_keys():
    row = {
        "id": "an-main",
        "prefix": "an",
        "api_type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "static-key",
        "models": '["claude-opus-4-7", "claude-sonnet-4-5"]',
        "allowed_models": '["claude-opus-4-7"]',
    }
    upstream_keys = [{"id": "uk-1", "key_value": "sk-one"}]
    configs = expand_gateway_provider(row, upstream_keys)
    assert len(configs) == 1
    assert configs[0].allowed_models == ["claude-opus-4-7"]


def test_expand_gateway_provider_defaults_allowed_models_empty():
    row = {
        "id": "openai-main",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "static-key",
        "models": "[]",
    }
    configs = expand_gateway_provider(row, [])
    assert configs[0].allowed_models == []


def test_expand_gateway_provider_plumbs_catalog_and_per_key_discoveries():
    row = {
        "id": "openai-main",
        "catalog_id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "static-key",
        "models": '["gpt-static"]',
        "default_model": "gpt-live",
        "live_models": 1,
        "selected_models": '["openai/gpt-custom"]',
    }
    configs = expand_gateway_provider(
        row,
        [
            {"id": "key-one", "key_value": "sk-one"},
            {"id": "key-two", "key_value": "sk-two"},
        ],
        custom_models=["gpt-custom"],
        discovered_models_by_key={"key-one": ["gpt-live"]},
    )

    assert configs[0].catalog_id == "openai"
    assert configs[0].default_model == "gpt-live"
    assert configs[0].live_models is True
    assert configs[0].known_models == ["gpt-static", "gpt-custom", "gpt-live"]
    assert configs[0].visible_models == ["gpt-custom"]
    assert configs[0].discovered_models == ["gpt-live"]
    assert configs[1].discovered_models is None


def test_expand_gateway_provider_disables_discovery_entitlements():
    row = {
        "id": "openai-main",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": None,
        "models": "[]",
        "live_models": 0,
    }
    configs = expand_gateway_provider(
        row,
        [{"id": "key-one", "key_value": "sk-one"}],
        discovered_models_by_key={"key-one": ["gpt-live"]},
    )
    assert configs[0].live_models is False
    assert configs[0].discovered_models is None
