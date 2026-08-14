"""503 responses must carry the full per-account attempt trail.

Before this, only the *last* failed account was surfaced in the
"All providers exhausted" detail/log, so multi-account routing was
unobservable (you couldn't tell whether fallback tried every account).
"""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.request_logs import list_request_logs
from janus.storage.settings import set_setting


async def _seed_and_reload(app) -> None:
    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.storage.database import init_db, seed_from_config

    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)


@pytest.fixture
async def app(tmp_path):
    accounts = [
        ProviderConfig(
            id=f"acct{i}",
            prefix="test",
            api_type="openai_compat",
            base_url="https://fake.local/v1",
            api_key=f"sk-test-{i}",
            models=["test-m1"],
        )
        for i in (1, 2)
    ]
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=accounts,
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.mark.asyncio
async def test_exhausted_detail_lists_every_attempted_account(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "rate limited"})
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]}
            r = await client.post("/v1/chat/completions", json=payload)

    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "All providers exhausted (2 attempt(s))" in detail
    assert "acct1: 429" in detail
    assert "acct2: 429" in detail

    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 503
    assert "acct1: 429" in logs[0]["error"]
    assert "acct2: 429" in logs[0]["error"]
