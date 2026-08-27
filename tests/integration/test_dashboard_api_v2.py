import asyncio
import json
from collections import Counter
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings

SECTIONS = (
    "overview",
    "usage",
    "analytics",
    "leaderboard",
    "request-logs",
    "inventory",
    "inventory-keys",
    "providers",
    "combos",
    "routing",
    "savers",
    "budgets",
    "keys",
    "tools",
    "pricing",
    "settings",
)
AUTH_KEY = "dashboard-auth-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="test-provider",
                prefix="test",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="provider-super-secret",
                models=["model-1"],
            )
        ],
        combos=[ComboConfig(name="test-combo", models=["test/model-1"])],
        api_keys=[AUTH_KEY],
    )
    return create_app(config=config)


def remote_transport(app):
    return ASGITransport(app=app, client=("203.0.113.10", 4321))


async def test_state_requires_dashboard_authentication(app):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard/api/v2/state/overview", headers={"Accept": "application/json"}
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/dashboard/login")
        response = await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)
        assert response.status_code == 200


async def test_lazy_dashboard_initialization_is_single_flight(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from janus.dashboard.routes import _ensure_db

    calls: Counter[str] = Counter()

    def fake_step(name: str):
        async def run(*_args, **_kwargs):
            calls[name] += 1
            if name == "init":
                await asyncio.sleep(0.01)

        return run

    monkeypatch.setattr("janus.dashboard.routes.init_db", fake_step("init"))
    monkeypatch.setattr("janus.storage.database.seed_from_config", fake_step("seed"))
    monkeypatch.setattr("janus.storage.settings.ensure_server_defaults", fake_step("defaults"))
    monkeypatch.setattr("janus.dashboard.reload.reload_providers", fake_step("providers"))
    monkeypatch.setattr("janus.dashboard.reload.reload_combos", fake_step("combos"))
    monkeypatch.setattr("janus.dashboard.reload.reload_savers", fake_step("savers"))
    monkeypatch.setattr("janus.dashboard.reload.reload_pricing", fake_step("pricing"))
    app.state._dashboard_db_ready = False
    request = SimpleNamespace(app=app)

    await asyncio.gather(_ensure_db(request), _ensure_db(request))

    assert calls == {
        "init": 1,
        "seed": 1,
        "defaults": 1,
        "providers": 1,
        "combos": 1,
        "savers": 1,
        "pricing": 1,
    }


async def test_lazy_dashboard_initialization_retries_after_failure(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from janus.dashboard.routes import _ensure_db

    calls: Counter[str] = Counter()

    async def flaky_init(*_args, **_kwargs):
        calls["init"] += 1
        if calls["init"] == 1:
            raise RuntimeError("temporary initialization failure")

    def fake_step(name: str):
        async def run(*_args, **_kwargs):
            calls[name] += 1

        return run

    monkeypatch.setattr("janus.dashboard.routes.init_db", flaky_init)
    monkeypatch.setattr("janus.storage.database.seed_from_config", fake_step("seed"))
    monkeypatch.setattr("janus.storage.settings.ensure_server_defaults", fake_step("defaults"))
    monkeypatch.setattr("janus.dashboard.reload.reload_providers", fake_step("providers"))
    monkeypatch.setattr("janus.dashboard.reload.reload_combos", fake_step("combos"))
    monkeypatch.setattr("janus.dashboard.reload.reload_savers", fake_step("savers"))
    monkeypatch.setattr("janus.dashboard.reload.reload_pricing", fake_step("pricing"))
    app.state._dashboard_db_ready = False
    request = SimpleNamespace(app=app)

    with pytest.raises(RuntimeError, match="temporary initialization failure"):
        await _ensure_db(request)
    assert app.state._dashboard_db_ready is False

    await _ensure_db(request)

    assert app.state._dashboard_db_ready is True
    assert calls == {
        "init": 2,
        "seed": 1,
        "defaults": 1,
        "providers": 1,
        "combos": 1,
        "savers": 1,
        "pricing": 1,
    }


async def test_lifespan_marks_dashboard_ready_before_first_request(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from janus.dashboard import reload as reload_module

    calls = 0
    original_reload = reload_module.reload_providers

    async def tracked_reload(target_app):
        nonlocal calls
        calls += 1
        await original_reload(target_app)

    async def no_pricing_sync(_app):
        return False

    monkeypatch.setattr(reload_module, "reload_providers", tracked_reload)
    monkeypatch.setattr("janus.app._pricing_catalog_needs_sync", no_pricing_sync)
    monkeypatch.setattr("janus.inventory.scheduler.scheduler_enabled", lambda: False)
    monkeypatch.setattr("janus.pricing.scheduler.pricing_scheduler_enabled", lambda: False)

    async with app.router.lifespan_context(app):
        assert app.state._dashboard_db_ready is True
        async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
            response = await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)

        assert response.status_code == 200
        assert calls == 1


async def test_successful_dashboard_mutation_invalidates_alert_cache(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        state = await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)
        generation = app.state._dashboard_alert_cache_generation
        response = await client.post("/dashboard/api/routing/cooldowns/clear", headers=AUTH_HEADERS)

    assert state.status_code == 200
    assert app.state._dashboard_alert_cache is None
    assert app.state._dashboard_alert_cache_generation == generation + 1
    assert response.status_code == 200


async def test_failed_dashboard_mutation_preserves_alert_cache(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        state = await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)
        cached = app.state._dashboard_alert_cache
        generation = app.state._dashboard_alert_cache_generation
        response = await client.post(
            "/dashboard/api/budgets",
            data={"key_select": "invalid", "daily_limit": "10", "warn_pct": "80"},
            headers=AUTH_HEADERS,
        )

    assert state.status_code == 200
    assert response.status_code == 422
    assert app.state._dashboard_alert_cache is cached
    assert app.state._dashboard_alert_cache_generation == generation


async def test_key_create_returns_plaintext_once_in_non_cacheable_json(app):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        unauthenticated = await client.post(
            "/dashboard/api/v2/keys",
            data={"name": "frontend-key"},
            headers={"Accept": "application/json"},
        )
        assert unauthenticated.status_code == 401

        response = await client.post(
            "/dashboard/api/v2/keys",
            data={
                "name": "frontend-key",
                "login_field": "1",
                "can_login": "",
                "allowed_models": "test/*, test-combo",
                "daily_budget": "2.5",
            },
            headers=AUTH_HEADERS,
        )
        state = await client.get("/dashboard/api/v2/state/keys", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    payload = response.json()
    plaintext = payload["api_key"]
    assert plaintext.startswith("sk-janus-")
    assert payload["key"]["name"] == "frontend-key"
    assert payload["key"]["can_login"] is False
    assert payload["key"]["allowed_models"] == ["test/*", "test-combo"]

    state_payload = state.json()
    assert plaintext not in json.dumps(state_payload)
    created = next(key for key in state_payload["data"]["keys"] if key["name"] == "frontend-key")
    assert created["budget"]["daily_limit"] == pytest.approx(2.5)


async def test_key_create_rejects_invalid_budget_before_creating_key(app):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/keys", headers=AUTH_HEADERS)
        response = await client.post(
            "/dashboard/api/v2/keys",
            data={"name": "should-not-exist", "daily_budget": "not-a-number"},
            headers=AUTH_HEADERS,
        )
        state = await client.get("/dashboard/api/v2/state/keys", headers=AUTH_HEADERS)

    assert response.status_code == 422
    assert all(key["name"] != "should-not-exist" for key in state.json()["data"]["keys"])


async def test_pricing_delete_accepts_model_ids_with_slashes(app):
    from janus.storage.pricing_db import (
        create_or_update_pricing_override,
        get_pricing_overrides,
    )

    model = "openai/gpt-5"
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/pricing", headers=AUTH_HEADERS)
        await create_or_update_pricing_override(
            app.state.db_path,
            {
                "model": model,
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
            },
        )
        response = await client.delete(
            f"/dashboard/api/pricing/{quote(model, safe='')}",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert model not in await get_pricing_overrides(app.state.db_path)


@pytest.mark.parametrize("section", SECTIONS)
async def test_every_supported_state_section_returns_stable_json(app, section):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        response = await client.get(f"/dashboard/api/v2/state/{section}", headers=AUTH_HEADERS)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["section"] == section
    assert isinstance(payload["alerts"], list)
    assert isinstance(payload["data"], dict)
    assert isinstance(payload["meta"], dict)


@pytest.mark.parametrize("section", ("overview", "usage"))
async def test_usage_summary_includes_cost_daily_series_and_timezone(app, section):
    from janus.storage.settings import set_setting
    from janus.storage.usage import record_usage

    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)
        await set_setting(app.state.db_path, "server_reporting_timezone", "Asia/Kolkata")
        await record_usage(
            app.state.db_path,
            provider_id="test-provider",
            model="test/model-1",
            input_tokens=120,
            output_tokens=30,
            status=200,
            cost=0.42,
        )
        response = await client.get(
            f"/dashboard/api/v2/state/{section}?days=7", headers=AUTH_HEADERS
        )

    assert response.status_code == 200
    payload = response.json()
    stats = payload["data"]["stats"]
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 120
    assert stats["total_output_tokens"] == 30
    assert stats["total_cost"] == pytest.approx(0.42)
    assert stats["daily"][-1]["requests"] == 1
    assert stats["period_days"] == 7
    assert stats["reporting_timezone"] == "Asia/Kolkata"
    assert payload["meta"]["query"] == {"days": 7}
    if section == "overview":
        assert payload["data"]["today_cost"] == pytest.approx(0.42)
        assert payload["data"]["reporting_timezone"] == "Asia/Kolkata"


async def test_unknown_state_section_returns_404(app):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        response = await client.get("/dashboard/api/v2/state/not-a-section", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_state_responses_do_not_leak_credentials(app):
    from janus.storage.settings import set_setting
    from janus.storage.upstream_keys import create_upstream_key

    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/state/overview", headers=AUTH_HEADERS)
        await create_upstream_key(
            app.state.db_path,
            provider_id="openai",
            key_value="inventory-super-secret",
            key_label="safe label",
            metadata={"api_key": "metadata-super-secret"},
        )
        await set_setting(app.state.db_path, "dashboard_username", "dashboard-operator")
        await set_setting(app.state.db_path, "dashboard_password_hash", "password-hash-secret")
        await set_setting(app.state.db_path, "dashboard_session_secret", "session-secret")
        await set_setting(app.state.db_path, "provider_credentials", "settings-secret")
        await set_setting(app.state.db_path, "server_api_token", "server-token-secret")

        payloads = []
        for section in SECTIONS:
            response = await client.get(f"/dashboard/api/v2/state/{section}", headers=AUTH_HEADERS)
            assert response.status_code == 200
            payloads.append(response.json())

    encoded = json.dumps(payloads)
    for secret in (
        AUTH_KEY,
        "provider-super-secret",
        "inventory-super-secret",
        "metadata-super-secret",
        "password-hash-secret",
        "session-secret",
        "settings-secret",
        "server-token-secret",
    ):
        assert secret not in encoded
    provider = payloads[SECTIONS.index("providers")]["data"]["providers"][0]
    assert "api_key" not in provider
    assert provider["has_api_key"] is True
    inventory_key = payloads[SECTIONS.index("inventory-keys")]["data"]["keys"][0]
    assert "key_value" not in inventory_key
    assert "key_hash" not in inventory_key
    assert "metadata" not in inventory_key
    assert inventory_key["key_masked"]
    settings = payloads[SECTIONS.index("settings")]["data"]
    assert "dashboard_username" not in settings
    assert "dashboard_password_set" not in settings
    assert settings["dashboard_access"] == {
        "mode": "api_key",
        "keys_url": "/dashboard/ui/keys",
        "localhost_requires_auth": True,
    }
    assert "server_api_token" not in settings["values"]


@pytest.mark.parametrize(
    ("section", "query"),
    (
        ("analytics", "days=0"),
        ("analytics", "dimension=invalid"),
        ("leaderboard", "sort=invalid"),
        ("inventory-keys", "sort=invalid"),
        ("inventory-keys", "status=not-a-status"),
        ("inventory-keys", "provider_id=missing"),
        ("inventory-keys", "dir=sideways"),
        ("request-logs", "limit=201"),
        ("request-logs", "offset=-1"),
        ("keys", "status=invalid"),
    ),
)
async def test_state_query_validation(app, section, query):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            f"/dashboard/api/v2/state/{section}?{query}", headers=AUTH_HEADERS
        )
    assert response.status_code == 422


async def test_inventory_state_echoes_valid_filters_and_pagination(app):
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard/api/v2/state/inventory-keys"
            "?provider_id=openai&status=active&sort=provider&dir=asc&limit=7&offset=0",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["query"] == {
        "provider_id": "openai",
        "status": "active",
        "search": "",
        "sort": "provider",
        "direction": "asc",
    }
    assert payload["meta"]["pagination"]["limit"] == 7
