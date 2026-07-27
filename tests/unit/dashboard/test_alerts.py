from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.alerts import collect_dashboard_alerts
from janus.storage.budgets import create_or_update_budget
from janus.storage.database import get_connection, init_db, seed_from_config


@pytest.fixture
async def db(tmp_path: Path) -> Path:
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t",
                prefix="t",
                api_type="openai_compat",
                base_url="https://test.local/v1",
                api_key="k",
                models=["m1"],
            )
        ],
    )
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_from_config(db_path, cfg)
    return db_path


def _fake_request() -> MagicMock:
    req = MagicMock()
    req.app.state.pricing_registry = MagicMock()
    req.app.state.pricing_registry.get.return_value = None
    return req


@pytest.mark.asyncio
async def test_budget_warning_alert(db: Path) -> None:
    await create_or_update_budget(db, key_id=None, daily_limit=10.0, warn_pct=80)
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO usage (cost, input_tokens, output_tokens, status) "
            "VALUES (9.0, 100, 50, 200)"
        )
        await conn.commit()
    result = await collect_dashboard_alerts(db, _fake_request())
    ids = [a.id for a in result["alerts"]]
    assert "budget:global" in ids
    assert result["summary"] in ("warning", "critical")


@pytest.mark.asyncio
async def test_no_providers_critical(db: Path) -> None:
    async with get_connection(db) as conn:
        await conn.execute("UPDATE providers SET is_enabled = 0")
        await conn.commit()
    result = await collect_dashboard_alerts(db, _fake_request())
    assert any(a.id == "setup:no_providers" for a in result["alerts"])
    assert result["summary"] == "critical"
