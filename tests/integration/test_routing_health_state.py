import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.alerts import invalidate_dashboard_alerts
from janus.dashboard.reload import reload_providers
from janus.storage.attempt_signals import record_attempt_signal
from janus.storage.database import get_connection, init_db, seed_from_config
from janus.storage.routing_health import mark_routing_unhealthy
from janus.storage.upstream_keys import create_upstream_key
from tests.fixtures.dashboard_auth import with_dashboard_auth

AUTH = {"Accept": "application/json"}


@pytest.fixture
async def app(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="prov-openai",
                prefix="openai",
                api_type="openai_compat",
                base_url="https://api.openai.example/v1",
                api_key="sk-config-not-real",
                models=["m1"],
            )
        ],
    )
    app = with_dashboard_auth(create_app(config=cfg))
    db = app.state.db_path
    await init_db(db)
    await seed_from_config(db, app.state.config)
    ids = []
    for index in range(3):
        record = await create_upstream_key(
            db,
            provider_id="openai",
            key_value=f"sk-health-fixture-{index:03d}",
            key_label=f"fixture {index}",
        )
        ids.append(str(record["id"]))
    async with get_connection(db) as conn:
        await conn.execute("UPDATE upstream_keys SET status='active', is_valid=1, is_usable=1")
        await conn.commit()
    app.state.test_key_ids = ids
    await reload_providers(app)
    return app


async def _state(app, section: str, **params: Any) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/dashboard/api/v2/state/{section}", headers=AUTH, params=params
        )
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


async def _flag(app, key_id: str) -> None:
    assert await mark_routing_unhealthy(
        app.state.db_path, key_id, reason="payment_error", status_code=402, at=time.time()
    )
    invalidate_dashboard_alerts(app)


async def test_routing_section_reports_unhealthy_accounts(app):
    bad = app.state.test_key_ids[0]
    await _flag(app, bad)

    payload = await _state(app, "routing")
    data = payload["data"]
    overview = data["overview"]
    assert overview["total_accounts"] == 3
    assert overview["unhealthy_accounts"] == 1
    assert overview["ready_accounts"] == 2
    accounts = {account["account_id"]: account for account in data["accounts"]}
    assert accounts[bad]["routing_unhealthy"] is True
    assert accounts[bad]["routing_health_reason"] == "payment_error"
    assert accounts[bad]["routing_health_status_code"] == 402
    assert accounts[bad]["routing_health_at"]
    healthy = [a for aid, a in accounts.items() if aid != bad]
    assert all(a["routing_unhealthy"] is False for a in healthy)
    assert [row["account_id"] for row in data["unhealthy"]] == [bad]


async def test_inventory_summary_counts_routing_unhealthy_as_attention(app):
    before = (await _state(app, "inventory"))["data"]["summary"]
    assert before["routing_unhealthy"] == 0
    await _flag(app, app.state.test_key_ids[0])
    after = (await _state(app, "inventory"))["data"]["summary"]
    assert after["routing_unhealthy"] == 1
    assert after["needs_attention"] == before["needs_attention"] + 1
    assert after["active"] == before["active"]


async def test_inventory_keys_filter_and_fields(app):
    bad = app.state.test_key_ids[1]
    await _flag(app, bad)
    payload = await _state(app, "inventory-keys", status="routing_unhealthy")
    keys = payload["data"]["keys"]
    assert [key["id"] for key in keys] == [bad]
    assert keys[0]["routing_health_status"] == "unhealthy"
    assert keys[0]["routing_health_reason"] == "payment_error"
    assert keys[0]["routing_health_status_code"] == 402
    assert "routing_unhealthy" in payload["data"]["filters"]["statuses"]


async def test_overview_provider_health_includes_credentials_and_attempts(app):
    await _flag(app, app.state.test_key_ids[0])
    for outcome, failure_class in (("ok", None), ("error", "payment_error")) * 3:
        await record_attempt_signal(
            app.state.db_path,
            model="m1",
            account_id=app.state.test_key_ids[0],
            provider_id="prov-openai",
            client_format="openai",
            streamed=False,
            outcome=outcome,
            status=200 if outcome == "ok" else 402,
            ttft_ms=None,
            duration_ms=10,
            output_tokens=0,
            output_tps=None,
            failure_class=failure_class,
        )
    health = (await _state(app, "overview"))["data"]["provider_health"]
    assert health["credentials_routing_unhealthy"] == 1
    assert health["credentials_needing_attention"] >= 1
    assert health["attempts"]["total"] == 6
    assert health["attempts"]["failed"] == 3
    assert health["attempts"]["success_rate"] == 50.0
    assert health["attempts"]["failed_by_class"] == {"payment_error": 3}


async def test_analytics_reports_attempt_success_separately(app):
    await record_attempt_signal(
        app.state.db_path,
        model="m1",
        account_id="a",
        provider_id="p",
        client_format="openai",
        streamed=False,
        outcome="error",
        status=402,
        ttft_ms=None,
        duration_ms=1,
        output_tokens=0,
        output_tps=None,
        failure_class="payment_error",
    )
    data = (await _state(app, "analytics", days=7))["data"]
    assert "success" in data
    assert data["attempts"]["failed"] == 1
    assert data["attempts"]["success_rate"] == 0.0


async def test_alerts_flag_unhealthy_credentials_with_contextual_link(app):
    await _flag(app, app.state.test_key_ids[0])
    payload = await _state(app, "inventory")
    alerts = {alert["id"]: alert for alert in payload["alerts"]}
    alert = alerts["inventory:routing_unhealthy"]
    assert alert["severity"] == "critical"
    assert alert["href"] == "/dashboard/ui/inventory/keys?status=routing_unhealthy"
