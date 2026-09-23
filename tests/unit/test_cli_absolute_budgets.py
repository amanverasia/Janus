import asyncio
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from janus.cli import app
from janus.storage.api_keys import create_key, list_keys
from janus.storage.budgets import get_budgets
from janus.storage.database import init_db

runner = CliRunner()


@pytest.fixture
def budget_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"server": {"data_dir": str(tmp_path)}}))
    db_path = tmp_path / "janus.db"
    asyncio.run(init_db(db_path))
    return str(config), db_path


def budgets(db_path: Path):
    return asyncio.run(get_budgets(db_path))


def test_cli_create_key_with_absolute_only_budget(budget_config):
    config, db_path = budget_config
    result = runner.invoke(
        app,
        ["keys", "create", "--name", "trial", "--absolute-budget", "25", "--config", config],
    )
    assert result.exit_code == 0
    rows = budgets(db_path)
    assert len(rows) == 1
    assert rows[0]["daily_limit"] is None
    assert rows[0]["absolute_limit"] == 25


def test_cli_key_budget_updates_and_clears_are_independent(budget_config):
    config, db_path = budget_config
    _, key = asyncio.run(create_key(db_path, "trial"))
    prefix = ["keys", "update", str(key["id"]), "--config", config]
    assert (
        runner.invoke(app, [*prefix, "--daily-budget", "5", "--absolute-budget", "25"]).exit_code
        == 0
    )
    assert runner.invoke(app, [*prefix, "--absolute-budget", "50"]).exit_code == 0
    assert budgets(db_path)[0]["daily_limit"] == 5
    assert budgets(db_path)[0]["absolute_limit"] == 50
    assert runner.invoke(app, [*prefix, "--daily-budget", "10"]).exit_code == 0
    assert budgets(db_path)[0]["absolute_limit"] == 50
    assert runner.invoke(app, [*prefix, "--clear-absolute-budget"]).exit_code == 0
    assert budgets(db_path)[0]["absolute_limit"] is None
    assert budgets(db_path)[0]["daily_limit"] == 10
    assert runner.invoke(app, [*prefix, "--clear-daily-budget"]).exit_code == 0
    assert budgets(db_path) == []


def test_cli_budgets_set_and_list_absolute_and_daily(budget_config):
    config, db_path = budget_config
    asyncio.run(create_key(db_path, "trial"))
    prefix = ["budgets", "set", "--key", "trial", "--config", config]
    created = runner.invoke(app, [*prefix, "--absolute", "25", "--warn", "90"])
    assert created.exit_code == 0
    assert "absolute limit = $25.00 (never resets)" in created.output
    assert budgets(db_path)[0]["daily_limit"] is None
    updated = runner.invoke(app, [*prefix, "--daily", "5"])
    assert updated.exit_code == 0
    assert "warn at 90%" in updated.output
    assert budgets(db_path)[0]["absolute_limit"] == 25
    listing = runner.invoke(app, ["budgets", "list", "--config", config])
    assert listing.exit_code == 0
    assert "Daily: $5.00" in listing.output
    assert "Absolute: $25.00" in listing.output
    assert "spent today: $0.00" in listing.output
    assert "spent total: $0.00" in listing.output
    assert "never resets" in listing.output
    assert runner.invoke(app, [*prefix, "--clear-daily"]).exit_code == 0
    assert budgets(db_path)[0]["daily_limit"] is None
    removed = runner.invoke(app, [*prefix, "--clear-absolute"])
    assert removed.exit_code == 0
    assert "Budget removed" in removed.output
    assert budgets(db_path) == []


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf"])
@pytest.mark.parametrize("option", ["--daily-budget", "--absolute-budget"])
def test_cli_create_validates_budgets_before_issuing_key(budget_config, option, value):
    config, db_path = budget_config
    result = runner.invoke(app, ["keys", "create", option, value, "--config", config])
    assert result.exit_code != 0
    assert asyncio.run(list_keys(db_path)) == []
    assert budgets(db_path) == []


def test_cli_update_rejects_unknown_key_and_conflicting_changes(budget_config):
    config, db_path = budget_config
    unknown = runner.invoke(
        app, ["keys", "update", "99999", "--absolute-budget", "25", "--config", config]
    )
    assert unknown.exit_code != 0
    assert "not found" in unknown.output
    _, key = asyncio.run(create_key(db_path, "trial"))
    conflicting = runner.invoke(
        app,
        [
            "keys",
            "update",
            str(key["id"]),
            "--name",
            "changed",
            "--absolute-budget",
            "25",
            "--clear-absolute-budget",
            "--config",
            config,
        ],
    )
    assert conflicting.exit_code != 0
    assert asyncio.run(list_keys(db_path))[0]["name"] == "trial"
    assert budgets(db_path) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--absolute", "25"],
        [],
        ["--daily", "5", "--warn", "nan"],
        ["--daily", "5", "--warn", "101"],
        ["--daily", "5", "--clear-daily"],
        ["--key", "missing", "--absolute", "25"],
    ],
)
def test_cli_budgets_set_rejects_invalid_requests_without_mutation(budget_config, arguments):
    config, db_path = budget_config
    result = runner.invoke(app, ["budgets", "set", *arguments, "--config", config])
    assert result.exit_code != 0
    assert budgets(db_path) == []
