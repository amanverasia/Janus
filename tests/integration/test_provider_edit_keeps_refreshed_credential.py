from __future__ import annotations

import json
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.inventory.provider_key_sync import _find_mirrored_key
from janus.storage.providers_db import update_provider
from janus.storage.upstream_keys import get_upstream_key, update_upstream_key
from tests.fixtures.dashboard_auth import with_dashboard_auth


def _blob(access: str, refresh: str) -> str:
    return json.dumps(
        {"access_token": access, "refresh_token": refresh, "expires_at": time.time() + 3600}
    )


@pytest.fixture
def app(tmp_path: Any) -> Any:
    config = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return with_dashboard_auth(create_app(config=config))


@pytest.fixture
async def client(app: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as session:
        yield session


async def test_blank_key_provider_edit_keeps_refreshed_credential(
    client: Any, app: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        "janus.inventory.provider_key_sync.schedule_upstream_recheck", lambda *a, **k: None
    )
    stale = _blob("sk-ant-oat01-stale", "sk-ant-ort01-stale")
    refreshed = _blob("sk-ant-oat01-fresh", "sk-ant-ort01-fresh")
    created = await client.post(
        "/dashboard/api/providers",
        data={
            "id": "claude-acct",
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": stale,
            "models": "claude-sonnet-4-5",
        },
    )
    assert created.status_code == 200
    db_path = app.state.db_path
    mirrored = await _find_mirrored_key(db_path, "claude-acct")
    assert mirrored is not None
    key_id = str(mirrored["id"])
    await update_upstream_key(
        db_path,
        key_id,
        {"key_value": refreshed, "status": "active", "is_valid": 1, "is_usable": 1},
    )
    await update_provider(db_path, "claude-acct", {"api_key": stale})

    edited = await client.put(
        "/dashboard/api/providers/claude-acct",
        data={
            "prefix": "claude",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "models": "claude-sonnet-4-5,claude-opus-4-1",
        },
    )

    assert edited.status_code == 200
    row = await get_upstream_key(db_path, key_id)
    assert row is not None
    assert row["key_value"] == refreshed
    assert row["status"] == "active"
    assert row["is_valid"] == 1
