import os
import tempfile

from janus.config.loader import load_config, resolve_vars


def test_resolve_vars():
    env = {"KEY": "secret123"}
    assert resolve_vars("api_${KEY}_end", env) == "api_secret123_end"
    assert resolve_vars({"key": "${KEY}"}, env) == {"key": "secret123"}


def test_resolve_vars_no_match():
    assert resolve_vars("${MISSING}", {}) == ""


def test_load_config_from_yaml():
    yaml_text = """
server:
  port: 3000
  host: 0.0.0.0
providers:
  - id: testp
    prefix: tp
    api_type: openai_compat
    base_url: https://test.com/v1
    api_key: ${TEST_KEY}
    models: [test-model]
"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        path = f.name
    try:
        os.environ["TEST_KEY"] = "mykey123"
        config = load_config(path)
        assert config.server.port == 3000
        assert config.providers[0].api_key == "mykey123"
    finally:
        os.unlink(path)
        del os.environ["TEST_KEY"]
