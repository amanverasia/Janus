import asyncio

import pytest
import yaml
from typer.testing import CliRunner

from janus.cli import app
from janus.storage.settings import get_setting

runner = CliRunner()


@pytest.mark.parametrize(
    ("key", "maximum"),
    [("server_dispatch_timeout_s", 600), ("server_dispatch_max_attempts", 100)],
)
def test_cli_dispatch_settings_share_validation_and_preserve_saved_limits(tmp_path, key, maximum):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"server": {"data_dir": str(tmp_path)}}))
    db_path = tmp_path / "janus.db"
    arguments = ["settings", "set", "--config", str(config), key]

    for valid in ("1", str(maximum)):
        result = runner.invoke(app, [*arguments, valid])
        assert result.exit_code == 0, result.output
        assert asyncio.run(get_setting(db_path, key)) == valid

    for invalid in ("", "invalid", "NaN", "inf", "1.5", "0", str(maximum + 1)):
        result = runner.invoke(app, [*arguments, invalid])
        assert result.exit_code == 1
        assert f"Invalid value for {key}" in result.output
        assert asyncio.run(get_setting(db_path, key)) == str(maximum)
