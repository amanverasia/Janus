import asyncio

import httpx
import pytest
import respx

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.pricing.scheduler import (
    pricing_scheduler_enabled,
    run_pricing_scheduler,
    sync_interval_hours,
)
from janus.pricing.sync import LITELLM_URL, OPENROUTER_URL
from janus.storage.database import init_db, seed_from_config


@pytest.fixture
def app(tmp_path):
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    application = create_app(config=cfg)
    return application


def test_sync_interval_hours_default(monkeypatch):
    monkeypatch.delenv("PRICING_SYNC_INTERVAL_HOURS", raising=False)
    assert sync_interval_hours() == 24.0


def test_sync_interval_hours_reads_env_live(monkeypatch):
    # Must be read inside the function (not module import time) so monkeypatching
    # from a test works even though janus.pricing.scheduler was already imported.
    monkeypatch.setenv("PRICING_SYNC_INTERVAL_HOURS", "0.0001")
    assert sync_interval_hours() == pytest.approx(0.0001)


def test_pricing_scheduler_enabled_default_true(monkeypatch):
    monkeypatch.delenv("PRICING_SYNC_ENABLED", raising=False)
    assert pricing_scheduler_enabled() is True


def test_pricing_scheduler_enabled_false(monkeypatch):
    monkeypatch.setenv("PRICING_SYNC_ENABLED", "false")
    assert pricing_scheduler_enabled() is False


@respx.mock
async def test_scheduler_respects_stop_event_promptly(app, monkeypatch):
    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)

    # Very short interval so the loop wakes up (or would sync) quickly if not stopped.
    monkeypatch.setenv("PRICING_SYNC_INTERVAL_HOURS", "0.0")

    respx.get(LITELLM_URL).mock(
        return_value=httpx.Response(
            200,
            json={"m": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}},
        )
    )
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    stop_event = asyncio.Event()
    task = asyncio.create_task(run_pricing_scheduler(app, stop_event))

    await asyncio.sleep(0.05)
    stop_event.set()

    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


async def test_scheduler_stops_immediately_when_stop_set_before_start(app, monkeypatch):
    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)

    monkeypatch.setenv("PRICING_SYNC_INTERVAL_HOURS", "24")

    stop_event = asyncio.Event()
    stop_event.set()

    await asyncio.wait_for(run_pricing_scheduler(app, stop_event), timeout=2.0)
