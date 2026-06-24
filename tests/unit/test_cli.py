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
