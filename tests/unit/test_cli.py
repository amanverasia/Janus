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


def _write_config(tmp_path) -> str:
    import yaml

    config_path = str(tmp_path / "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump({"server": {"data_dir": str(tmp_path)}}, f)
    return config_path


def test_inventory_encrypt_keys_covers_provider_credentials(tmp_path, monkeypatch):
    import asyncio

    from cryptography.fernet import Fernet

    from janus.storage.database import get_connection, init_db
    from janus.storage.providers_db import create_provider
    from janus.storage.upstream_keys import create_upstream_key

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "janus.db"
    asyncio.run(init_db(db_path))
    asyncio.run(
        create_provider(
            db_path,
            {
                "id": "openai",
                "prefix": "openai",
                "api_type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-provider",
                "models": [],
            },
        )
    )
    asyncio.run(create_upstream_key(db_path, provider_id="openai", key_value="sk-upstream"))
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

    result = runner.invoke(app, ["inventory", "encrypt-keys", "--config", config_path])

    assert result.exit_code == 0
    assert "Encrypted 1 upstream key(s) and 1 provider credential(s)" in result.output
    assert "Provider credentials: 1 encrypted, 0 plaintext" in result.output

    async def _stored_values():
        async with get_connection(db_path) as db:
            async with db.execute("SELECT api_key FROM providers") as cur:
                provider = await cur.fetchone()
            async with db.execute("SELECT key_value FROM upstream_keys") as cur:
                upstream = await cur.fetchone()
        return provider["api_key"], upstream["key_value"]

    provider_value, upstream_value = asyncio.run(_stored_values())
    assert provider_value.startswith("enc:v1:")
    assert upstream_value.startswith("enc:v1:")


def test_pricing_sync_success(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)

    async def fake_fetch_and_sync(db_path):
        return 42

    monkeypatch.setattr("janus.pricing.sync.fetch_and_sync", fake_fetch_and_sync)
    result = runner.invoke(app, ["pricing", "sync", "--config", config_path])
    assert result.exit_code == 0
    assert "42" in result.output


def test_pricing_sync_failure_exits_nonzero(tmp_path, monkeypatch):
    from janus.pricing.sync import PricingSyncError

    config_path = _write_config(tmp_path)

    async def fake_fetch_and_sync(db_path):
        raise PricingSyncError("both sources failed")

    monkeypatch.setattr("janus.pricing.sync.fetch_and_sync", fake_fetch_and_sync)
    result = runner.invoke(app, ["pricing", "sync", "--config", config_path])
    assert result.exit_code == 1
    assert "both sources failed" in result.output


def test_pricing_backfill_updates_rows_and_notes_today(tmp_path):
    import asyncio

    from janus.storage.database import init_db
    from janus.storage.usage import record_usage

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "janus.db"
    asyncio.run(init_db(db_path))
    asyncio.run(
        record_usage(
            db_path,
            provider_id="p",
            model="gpt-4o-mini",
            input_tokens=1_000_000,
            output_tokens=500_000,
            status=200,
            cost=0.0,
        )
    )

    result = runner.invoke(app, ["pricing", "backfill", "--config", config_path])
    assert result.exit_code == 0
    assert "Updated 1 row" in result.output
    assert "today's measured spend increased" in result.output


def test_pricing_backfill_dry_run_writes_nothing(tmp_path):
    import asyncio

    from janus.storage.database import get_connection, init_db
    from janus.storage.usage import record_usage

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "janus.db"
    asyncio.run(init_db(db_path))
    asyncio.run(
        record_usage(
            db_path,
            provider_id="p",
            model="gpt-4o-mini",
            input_tokens=1_000_000,
            output_tokens=500_000,
            status=200,
            cost=0.0,
        )
    )

    result = runner.invoke(app, ["pricing", "backfill", "--dry-run", "--config", config_path])
    assert result.exit_code == 0
    assert "Would update 1 row" in result.output
    assert "today's measured spend" not in result.output

    async def _read_cost():
        async with get_connection(db_path) as db:
            async with db.execute("SELECT cost FROM usage") as cur:
                row = await cur.fetchone()
        return row["cost"]

    assert asyncio.run(_read_cost()) == 0.0


def test_pricing_backfill_respects_days(tmp_path):
    import asyncio

    from janus.storage.database import get_connection, init_db
    from janus.storage.usage import record_usage

    config_path = _write_config(tmp_path)
    db_path = tmp_path / "janus.db"
    asyncio.run(init_db(db_path))
    asyncio.run(
        record_usage(
            db_path,
            provider_id="p",
            model="gpt-4o-mini",
            input_tokens=1_000_000,
            output_tokens=500_000,
            status=200,
            cost=0.0,
        )
    )

    async def _age_row():
        async with get_connection(db_path) as db:
            await db.execute("UPDATE usage SET timestamp = datetime('now', '-90 days')")
            await db.commit()

    asyncio.run(_age_row())

    result = runner.invoke(app, ["pricing", "backfill", "--days", "30", "--config", config_path])
    assert result.exit_code == 0
    assert "Updated 0 row" in result.output
