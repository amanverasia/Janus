import os
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from janus.cli import app

runner = CliRunner()


def test_config_init():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.yaml")
        result = runner.invoke(app, ["config-init", "--path", config_path])
        assert result.exit_code == 0
        assert Path(config_path).exists()
        content = Path(config_path).read_text()
        assert "server" in content
        assert "providers" in content


def test_config_init_already_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.yaml")
        Path(config_path).write_text("existing: true")
        result = runner.invoke(app, ["config-init", "--path", config_path])
        assert result.exit_code == 0
        assert "already exists" in result.output


def test_config_path():
    result = runner.invoke(app, ["config-path"])
    assert result.exit_code == 0
    assert ".janus" in result.output


def test_keys_create_and_list(tmp_path):
    import yaml

    config_path = str(tmp_path / "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    result = runner.invoke(app, ["keys", "create", "--name", "test", "--config", config_path])
    assert result.exit_code == 0
    assert "sk-janus-" in result.output
    result2 = runner.invoke(app, ["keys", "list", "--config", config_path])
    assert result2.exit_code == 0
    assert "test" in result2.output


def test_usage_stats_empty(tmp_path):
    import yaml

    config_path = str(tmp_path / "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    result = runner.invoke(app, ["usage", "stats", "--config", config_path])
    assert result.exit_code == 0
    assert "Total requests: 0" in result.output


def test_keys_revoke(tmp_path):
    import yaml

    config_path = str(tmp_path / "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    runner.invoke(app, ["keys", "create", "--name", "torevoke", "--config", config_path])
    result = runner.invoke(app, ["keys", "revoke", "1", "--config", config_path])
    assert result.exit_code == 0
    assert "Revoked" in result.output
