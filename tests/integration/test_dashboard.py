import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings


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
    return create_app(config=cfg)


@pytest.mark.asyncio
async def test_dashboard_overview(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Janus" in r.text
        assert "http://test/v1" in r.text
        assert "Quick setup" in r.text


@pytest.mark.asyncio
async def test_dashboard_providers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "t/" in r.text or "t" in r.text


@pytest.mark.asyncio
async def test_dashboard_combos(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/combos")
        assert r.status_code == 200
        assert "stk" in r.text


@pytest.mark.asyncio
async def test_usage_page_has_live_section(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/usage")
        assert r.status_code == 200
        assert "Live requests" in r.text
        assert "/dashboard/api/usage/live" in r.text


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
        r = await client.get("/dashboard/combos")
        assert r.status_code == 200
        assert "Connected providers" in r.text
        assert 'data-prefix="t"' in r.text
        assert 'data-model="t/m1"' in r.text


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
    app = create_app(config=cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/combos")
        assert r.status_code == 200
        assert 'data-model="t/m1"' in r.text
        assert 'data-model="t/m2"' not in r.text


@pytest.mark.asyncio
async def test_dashboard_keys_page(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/keys")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_keys_create(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/keys", data={"name": "test-key"})
        assert r.status_code == 200
        assert "sk-janus-" in r.text


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
        r = await client.get("/dashboard/usage")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_settings_page_shows_account_strategy(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/settings")
        assert r.status_code == 200
        body = r.text
        assert "Account Selection Strategy" in body
        assert "server_account_strategy" in body
        assert "server_sticky_limit" in body


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
        r = await client.get("/dashboard/settings")
        assert r.status_code == 200
        body = r.text
        assert "Combo Routing" in body
        assert "combo_strategy" in body
        assert "combo_sticky_limit" in body
        assert "combo_fusion_judge" in body
        assert "combo_fusion_min_panel" in body
        assert "combo_fusion_straggler_grace_s" in body
        assert "combo_fusion_hard_timeout_s" in body
        # placeholder for the judge model input
        assert "prefix/model" in body


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
        # Visit the settings page first so server defaults are seeded into the DB.
        await client.get("/dashboard/settings")
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
        await client.get("/dashboard/settings")
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
        await client.get("/dashboard/settings")
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
