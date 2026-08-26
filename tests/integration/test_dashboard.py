import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from tests.fixtures.dashboard_auth import with_dashboard_auth


@pytest.fixture
def app(tmp_path):
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
        combos=[ComboConfig(name="stk", models=["t/m1"])],
    )
    return with_dashboard_auth(create_app(config=cfg))


@pytest.mark.asyncio
async def test_dashboard_overview(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/overview")
        assert r.status_code == 200
        assert r.json()["section"] == "overview"
        assert r.json()["data"]["base_url"] == "http://test/v1"
        assert set(r.json()["data"]["setup_checklist"]) == {
            "has_providers",
            "has_keys",
            "has_requests",
        }


@pytest.mark.asyncio
async def test_dashboard_providers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/providers")
        assert r.status_code == 200
        provider = next(row for row in r.json()["data"]["providers"] if row["prefix"] == "t")
        assert provider["catalog_model_count"] == 1
        assert provider["visible_model_count"] == 1
        assert provider["gateway_account_count"] == 1
        assert provider["account_count"] >= 0


@pytest.mark.asyncio
async def test_dashboard_combos(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/combos")
        assert r.status_code == 200
        assert any(row["name"] == "stk" for row in r.json()["data"]["combos"])


@pytest.mark.asyncio
async def test_usage_page_has_live_section(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/usage")
        assert r.status_code == 200
        assert r.json()["section"] == "usage"
        snapshot = await client.get("/dashboard/api/usage/snapshot")
        assert snapshot.status_code == 200
        assert "inflight" in snapshot.json()


@pytest.mark.asyncio
async def test_usage_live_sse_snapshot_and_event(app):
    # The SSE generator never ends on its own, and httpx's ASGITransport waits
    # for the app coroutine to finish — so drive the endpoint's stream directly.
    from janus.dashboard.live import get_bus, reset_bus
    from janus.dashboard.routes import usage_live_stream

    reset_bus()
    try:

        class _FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        response = await usage_live_stream(_FakeRequest())  # type: ignore[arg-type]
        assert response.media_type == "text/event-stream"
        stream = response.body_iterator

        first = await asyncio.wait_for(anext(stream), timeout=5)
        snap = json.loads(first.decode().removeprefix("data: "))
        assert snap["type"] == "snapshot"
        assert snap["inflight"] == 0

        get_bus().record_completed(model="t/m1", client_key_label="alice", status=200, cost=0.01)
        chunk = await asyncio.wait_for(anext(stream), timeout=5)
        event = json.loads(chunk.decode().removeprefix("data: "))
        assert event["type"] == "request"
        assert event["model"] == "t/m1"
        assert event["user"] == "alice"

        await stream.aclose()
    finally:
        reset_bus()


@pytest.mark.asyncio
async def test_combo_modal_lists_wired_providers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/combos")
        assert r.status_code == 200
        wired = r.json()["data"]["wired_providers"]
        assert wired == [{"prefix": "t", "models": ["m1"], "accounts": 1}]


@pytest.mark.asyncio
async def test_combo_modal_hides_allowlist_blocked_models(tmp_path):
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t",
                prefix="t",
                api_type="openai_compat",
                base_url="https://test.local/v1",
                api_key="k",
                models=["m1", "m2"],
                allowed_models=["m1"],
            )
        ],
    )
    app = with_dashboard_auth(create_app(config=cfg))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/combos")
        assert r.status_code == 200
        wired = r.json()["data"]["wired_providers"]
        assert wired == [{"prefix": "t", "models": ["m1"], "accounts": 1}]


@pytest.mark.asyncio
async def test_dashboard_keys_page(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/keys")
        assert r.status_code == 200
        assert r.json()["section"] == "keys"


@pytest.mark.asyncio
async def test_dashboard_keys_create(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/keys", data={"name": "test-key"})
        assert r.status_code == 200
        assert "sk-janus-" in r.text
        assert "Copy key" in r.text
        assert 'data-copy-label="API key"' in r.text
        assert "legacyCopy" in r.text


@pytest.mark.asyncio
async def test_dashboard_existing_key_copies_unmasked_prefix(app):
    from janus.storage.api_keys import create_key
    from janus.storage.database import init_db

    await init_db(app.state.db_path)
    _, record = await create_key(app.state.db_path, "prefix-copy")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/api/v2/state/keys")

    prefix = record["prefix"]
    assert response.status_code == 200
    keys = response.json()["data"]["keys"]
    assert any(row["prefix"] == prefix for row in keys)


@pytest.mark.asyncio
async def test_dashboard_keys_create_with_scopes(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/keys",
            data={
                "name": "scoped",
                "login_field": "1",
                "can_login": "",
                "allowed_models": "test/*, combo",
                "daily_budget": "2.5",
            },
        )
        assert r.status_code == 200
        assert "sk-janus-" in r.text
        assert "No" in r.text or "api" in r.text.lower()
        assert "test/*" in r.text


@pytest.mark.asyncio
async def test_dashboard_keys_edit_can_remove_login_access(app):
    from janus.storage.api_keys import create_key, get_key_policy
    from janus.storage.database import init_db

    await init_db(app.state.db_path)
    _, record = await create_key(app.state.db_path, "dashboard-user", can_login=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        edit = await client.get(f"/dashboard/api/keys/{record['id']}/edit")
        assert edit.status_code == 200
        assert 'method="post"' in edit.text

        response = await client.post(
            f"/dashboard/api/keys/{record['id']}",
            data={
                "name": "dashboard-user",
                "login_field": "1",
                "models_field": "1",
                "allowed_models": "",
            },
        )
        assert response.status_code == 200

    policy = await get_key_policy(app.state.db_path, int(record["id"]))
    assert policy is not None
    assert policy["can_login"] is False


@pytest.mark.asyncio
async def test_dashboard_login_rejects_api_only_key(app):
    from janus.storage.api_keys import create_key
    from janus.storage.database import init_db

    await init_db(app.state.db_path)
    full_key, _ = await create_key(app.state.db_path, "api-only", can_login=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/login",
            data={"api_key": full_key, "next": "/dashboard"},
        )
        assert r.status_code == 401
        assert "cannot access the dashboard" in r.text


@pytest.mark.asyncio
async def test_dashboard_keys_revoke(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create a key first
        await client.post("/dashboard/api/keys", data={"name": "torevoke"})
        # Revoke it
        r = await client.delete("/dashboard/api/keys/1")
        assert r.status_code == 200
        assert "Revoked" in r.text or "revoked" in r.text.lower()


@pytest.mark.asyncio
async def test_dashboard_usage(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/usage")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_settings_page_shows_account_strategy(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/settings")
        assert r.status_code == 200
        status = r.json()["data"]["status"]
        assert "account_strategy" in status
        assert "sticky_limit" in status


@pytest.mark.asyncio
async def test_settings_post_updates_account_strategy(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/settings",
            content="key=server_account_strategy&value=sticky_rr",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
    db_path = app.state.db_path
    assert await get_setting(db_path, "server_account_strategy") == "sticky_rr"


@pytest.mark.asyncio
async def test_settings_page_shows_combo_routing(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/settings")
        assert r.status_code == 200
        status = r.json()["data"]["status"]
        assert "combo_strategy" in status
        assert "combo_sticky_limit" in status
        assert "combo_fusion_judge" in status
        assert "combo_fusion_min_panel" in status
        assert "combo_fusion_straggler_grace_s" in status
        assert "combo_fusion_hard_timeout_s" in status


@pytest.mark.asyncio
async def test_settings_post_updates_combo_strategy(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/settings",
            content="key=combo_strategy&value=fusion",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_strategy") == "fusion"


@pytest.mark.asyncio
async def test_settings_post_updates_combo_fusion_settings(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for key, value in [
            ("combo_sticky_limit", "5"),
            ("combo_fusion_judge", "openai/gpt-4o-mini"),
            ("combo_fusion_min_panel", "3"),
            ("combo_fusion_straggler_grace_s", "12.5"),
            ("combo_fusion_hard_timeout_s", "120"),
        ]:
            r = await client.post(
                "/dashboard/api/settings",
                content=f"key={key}&value={value}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert r.status_code == 200, key
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_sticky_limit") == "5"
    assert await get_setting(db_path, "combo_fusion_judge") == "openai/gpt-4o-mini"
    assert await get_setting(db_path, "combo_fusion_min_panel") == "3"
    assert await get_setting(db_path, "combo_fusion_straggler_grace_s") == "12.5"
    assert await get_setting(db_path, "combo_fusion_hard_timeout_s") == "120"


@pytest.mark.asyncio
async def test_settings_post_can_clear_combo_fusion_judge(app):
    from janus.storage.database import init_db
    from janus.storage.settings import get_setting, set_setting

    await init_db(app.state.db_path)
    await set_setting(app.state.db_path, "combo_fusion_judge", "openai/gpt-4o-mini")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/settings",
            content="key=combo_fusion_judge&value=",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 200
    assert await get_setting(app.state.db_path, "combo_fusion_judge") == ""


@pytest.mark.asyncio
async def test_settings_post_rejects_invalid_combo_strategy(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/settings",
            content="key=combo_strategy&value=bogus",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 400
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_strategy") is None


@pytest.mark.asyncio
async def test_settings_post_rejects_invalid_combo_sticky_limit(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/dashboard/api/settings",
            content="key=combo_sticky_limit&value=notanumber",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 400
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_sticky_limit") is None


@pytest.mark.asyncio
async def test_settings_post_rejects_invalid_combo_fusion_min_panel(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/settings")
        before = await get_setting(app.state.db_path, "combo_fusion_min_panel")
        r = await client.post(
            "/dashboard/api/settings",
            content="key=combo_fusion_min_panel&value=0",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 400
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_fusion_min_panel") == before


@pytest.mark.asyncio
async def test_settings_post_rejects_invalid_combo_fusion_timeout(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/settings")
        before = await get_setting(app.state.db_path, "combo_fusion_hard_timeout_s")
        for bad in ("not-a-float", "nan", "inf", "-inf", "0", "-5", "3601"):
            r = await client.post(
                "/dashboard/api/settings",
                content=f"key=combo_fusion_hard_timeout_s&value={bad}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert r.status_code == 400, f"value {bad!r} should be rejected"
    db_path = app.state.db_path
    assert await get_setting(db_path, "combo_fusion_hard_timeout_s") == before


@pytest.mark.asyncio
async def test_settings_post_combo_fusion_grace_bounds(app):
    from janus.storage.settings import get_setting

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/settings")
        for bad in ("nan", "inf", "-1", "3601"):
            r = await client.post(
                "/dashboard/api/settings",
                content=f"key=combo_fusion_straggler_grace_s&value={bad}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert r.status_code == 400, f"value {bad!r} should be rejected"
        # Grace of exactly 0 is valid (no straggler window).
        r = await client.post(
            "/dashboard/api/settings",
            content="key=combo_fusion_straggler_grace_s&value=0",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
    assert await get_setting(app.state.db_path, "combo_fusion_straggler_grace_s") == "0"


@pytest.mark.asyncio
async def test_overview_status_strip(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/overview")
        assert r.status_code == 200
        assert "alerts" in r.json()
        assert "alert_summary" in r.json()["meta"]


@pytest.mark.asyncio
async def test_usage_snapshot_endpoint(app):
    from janus.dashboard.live import get_bus, reset_bus

    reset_bus()
    get_bus().record_completed(model="t/m1", status=200, input_tokens=1, output_tokens=2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/usage/snapshot")
        assert r.status_code == 200
        data = r.json()
        assert "inflight" in data
        assert "recent" in data
        assert len(data["recent"]) >= 1
    reset_bus()


@pytest.mark.asyncio
async def test_analytics_unpriced_banner(app):
    from janus.storage.database import get_connection, init_db

    db_path = app.state.db_path
    await init_db(db_path)
    async with get_connection(db_path) as conn:
        await conn.execute(
            "INSERT INTO usage (model, cost, input_tokens, output_tokens, status, provider_id) "
            "VALUES ('unknown/model-x', 0, 500, 200, 200, 't')"
        )
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/v2/state/analytics?days=30")
        assert r.status_code == 200
        assert "unknown/model-x" in r.json()["data"]["unpriced_model_ids"]
