import httpx
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import _drain_stream_persist_tasks
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import (
    reload_combos,
    reload_pricing,
    reload_providers,
    reload_savers,
)
from janus.routing.fallback import ROUTING_HEALTH_FAILURE_THRESHOLD
from janus.storage.attempt_signals import list_attempt_signals
from janus.storage.database import init_db, seed_from_config
from janus.storage.settings import set_setting
from tests.fixtures.dashboard_auth import with_dashboard_auth

GOOD_BODY = {
    "id": "r1",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
}


def _provider(pid: str, host: str) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        prefix="test",
        api_type="openai_compat",
        base_url=f"https://{host}/v1",
        api_key=f"sk-{pid}",
        models=["m1"],
    )


def _body():
    return {"model": "test/m1", "messages": [{"role": "user", "content": "synthetic"}]}


async def _app(tmp_path, *, cooldowns: bool):
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            _provider("dead-a", "a.local"),
            _provider("dead-b", "b.local"),
            _provider("live-c", "c.local"),
        ],
    )
    app = with_dashboard_auth(create_app(config=cfg))
    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, app.state.config)
    await set_setting(app.state.db_path, "server_account_strategy", "fill_first")
    await set_setting(
        app.state.db_path, "server_cooldowns_enabled", "true" if cooldowns else "false"
    )
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)
    return app


async def _attempt_counts(app) -> list[int]:
    await _drain_stream_persist_tasks()
    rows = await list_attempt_signals(app.state.db_path, limit=500)
    per_request: dict[str, int] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        per_request[row["request_id"]] = per_request.get(row["request_id"], 0) + 1
    return list(per_request.values())


@respx.mock
async def test_dead_accounts_demoted_after_threshold_even_with_cooldowns_disabled(tmp_path):
    app = await _app(tmp_path, cooldowns=False)
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(402))
    respx.post("https://b.local/v1/chat/completions").mock(return_value=httpx.Response(401))
    respx.post("https://c.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(ROUTING_HEALTH_FAILURE_THRESHOLD):
            assert (await client.post("/v1/chat/completions", json=_body())).status_code == 200
        handler = app.state.fallback_handler
        assert handler.is_routing_unhealthy("dead-a")
        assert handler.is_routing_unhealthy("dead-b")
        assert not handler.is_routing_unhealthy("live-c")
        assert (await client.post("/v1/chat/completions", json=_body())).status_code == 200
    counts = await _attempt_counts(app)
    assert counts[:ROUTING_HEALTH_FAILURE_THRESHOLD] == [3] * ROUTING_HEALTH_FAILURE_THRESHOLD
    assert counts[-1] == 1


@respx.mock
async def test_rate_limits_never_flag_accounts(tmp_path):
    app = await _app(tmp_path, cooldowns=False)
    respx.post("https://a.local/v1/chat/completions").mock(return_value=httpx.Response(429))
    respx.post("https://b.local/v1/chat/completions").mock(return_value=httpx.Response(503))
    respx.post("https://c.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(ROUTING_HEALTH_FAILURE_THRESHOLD + 2):
            assert (await client.post("/v1/chat/completions", json=_body())).status_code == 200
    handler = app.state.fallback_handler
    assert not handler.is_routing_unhealthy("dead-a")
    assert not handler.is_routing_unhealthy("dead-b")
