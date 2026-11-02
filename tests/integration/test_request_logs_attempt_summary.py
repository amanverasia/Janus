import json

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.attempt_signals import record_attempt_signal
from janus.storage.database import init_db
from janus.storage.outcomes import record_request_outcome
from janus.storage.request_logs import record_request_log
from janus.storage.upstream_keys import create_upstream_key

AUTH_KEY = "dashboard-auth-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
RAW_CREDENTIAL = "sk-raw-credential-must-not-leak"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[],
        api_keys=[AUTH_KEY],
    )
    return create_app(config=config)


async def _signal(db, request_id, order, account_id, *, ok=False):
    await record_attempt_signal(
        db,
        request_id=request_id,
        attempt_order=order,
        model="openai/m1",
        account_id=account_id,
        provider_id=f"openai::{account_id}",
        client_format="openai",
        streamed=False,
        outcome="ok" if ok else "error",
        status=200 if ok else 402,
        ttft_ms=None,
        duration_ms=30,
        output_tokens=0,
        output_tps=None,
        failure_class=None if ok else "payment_error",
        cooldown_decision="none" if ok else "applied",
        cooldown_seconds=None if ok else 300,
    )


async def _seed(app):
    db = app.state.db_path
    await init_db(db)
    key = await create_upstream_key(
        db, provider_id="openai", key_value=RAW_CREDENTIAL, key_label="Synthetic"
    )
    await record_request_log(db, model="legacy", status=200)
    await record_request_log(db, request_id="r-clean", model="openai/m1", status=200)
    await record_request_outcome(db, request_id="r-clean", model="m1", status=200, attempts=1)
    await _signal(db, "r-clean", 1, key["id"], ok=True)
    await record_request_log(db, request_id="r-recovered", model="openai/m1", status=200)
    await record_request_outcome(db, request_id="r-recovered", model="m1", status=200, attempts=20)
    for order in range(1, 20):
        await _signal(db, "r-recovered", order, key["id"])
    await _signal(db, "r-recovered", 20, key["id"], ok=True)
    await record_request_log(db, request_id="r-pruned", model="openai/m1", status=200)
    await record_request_outcome(db, request_id="r-pruned", model="m1", status=200, attempts=3)
    return key


async def _state(app, query=""):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 1)), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/dashboard/api/v2/state/request-logs{query}", headers=AUTH_HEADERS
        )
    assert response.status_code == 200, response.text
    return response.json()


async def test_rows_carry_attempt_counts_without_leaking_credentials(app):
    key = await _seed(app)
    payload = await _state(app)
    rows = {row["request_id"]: row for row in payload["data"]["logs"]}

    recovered = rows["r-recovered"]
    assert recovered["status"] == 200
    assert recovered["attempt_count"] == 20
    assert recovered["failed_attempt_count"] == 19
    assert recovered["attempt_details_available"] is True

    assert rows["r-clean"]["attempt_count"] == 1
    assert rows["r-clean"]["failed_attempt_count"] == 0

    pruned = rows["r-pruned"]
    assert pruned["attempt_count"] == 3
    assert pruned["failed_attempt_count"] is None
    assert pruned["attempt_details_available"] is False

    legacy = rows[None]
    assert legacy["attempt_count"] is None
    assert legacy["failed_attempt_count"] is None
    assert legacy["attempt_details_available"] is False

    assert payload["data"]["failed_attempts_only"] is False
    encoded = json.dumps(payload)
    assert RAW_CREDENTIAL not in encoded
    assert key["id"] not in encoded


async def test_failed_attempts_filter_scopes_rows_and_pagination(app):
    await _seed(app)
    payload = await _state(app, "?failed_attempts=true")
    assert [row["request_id"] for row in payload["data"]["logs"]] == ["r-recovered"]
    assert payload["data"]["failed_attempts_only"] is True
    assert payload["meta"]["pagination"]["total"] == 1


async def test_detail_reports_attempt_summary_and_masks_accounts(app):
    key = await _seed(app)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 1)), base_url="http://test"
    ) as client:
        logs = (await _state(app))["data"]["logs"]
        row = next(item for item in logs if item["request_id"] == "r-recovered")
        response = await client.get(
            f"/dashboard/api/request-logs/{row['id']}?attempt_limit=5", headers=AUTH_HEADERS
        )
    detail = response.json()
    assert detail["attempt_summary"] == {"total": 20, "failed": 19, "omitted": 15}
    assert len(detail["attempts"]) == 5
    encoded = json.dumps(detail)
    assert RAW_CREDENTIAL not in encoded
    assert key["id"] not in encoded
