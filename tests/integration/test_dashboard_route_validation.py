from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget
from janus.storage.providers_db import get_provider
from janus.storage.settings import get_setting
from tests.fixtures.dashboard_auth import with_dashboard_auth


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return with_dashboard_auth(create_app(config=config))


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as session:
        yield session


def provider_form(**overrides: str) -> dict[str, str]:
    return {
        "id": "validation-provider",
        "prefix": "validation",
        "api_type": "openai_compat",
        "base_url": "https://provider.example/v1",
        "api_key": "provider-secret",
        "models": "model-1",
        **overrides,
    }


async def test_settings_reject_unknown_keys_and_validate_request_log_retention(client, app):
    unknown = await client.post(
        "/dashboard/api/settings",
        data={"key": "unknown-secret-setting", "value": "sensitive-value"},
    )
    invalid = await client.post(
        "/dashboard/api/settings",
        data={"key": "server_request_log_retention", "value": "5001"},
    )
    valid = await client.post(
        "/dashboard/api/settings",
        data={"key": "server_request_log_retention", "value": "5000"},
    )

    assert unknown.status_code == 400
    assert unknown.headers["content-type"].startswith("application/json")
    assert "unknown-secret-setting" not in unknown.text
    assert "sensitive-value" not in unknown.text
    assert await get_setting(app.state.db_path, "unknown-secret-setting") is None
    assert invalid.status_code == 400
    assert valid.status_code == 200
    assert await get_setting(app.state.db_path, "server_request_log_retention") == "5000"


async def test_usage_retention_setting_is_validated(client, app):
    for value in ("6", "3651", "not-a-number"):
        response = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_usage_retention_days", "value": value},
        )
        assert response.status_code == 400

    for value in ("7", "3650"):
        response = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_usage_retention_days", "value": value},
        )
        assert response.status_code == 200
        assert await get_setting(app.state.db_path, "server_usage_retention_days") == value


async def test_missing_dashboard_mutation_ids_return_404(client):
    responses = [
        await client.delete("/dashboard/api/keys/999"),
        await client.delete("/dashboard/api/budgets/999"),
        await client.patch("/dashboard/api/providers/missing/toggle"),
        await client.delete("/dashboard/api/providers/missing"),
        await client.put(
            "/dashboard/api/combos/999", data={"name": "missing", "models": "model-1"}
        ),
        await client.delete("/dashboard/api/combos/999"),
    ]

    assert [response.status_code for response in responses] == [404] * len(responses)
    assert all(
        response.headers["content-type"].startswith("application/json") for response in responses
    )


async def test_duplicate_provider_creation_returns_safe_conflict(client):
    created = await client.post("/dashboard/api/providers", data=provider_form())
    duplicate = await client.post(
        "/dashboard/api/providers", data=provider_form(api_key="duplicate-secret")
    )

    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.headers["content-type"].startswith("application/json")
    assert "duplicate-secret" not in duplicate.text


@pytest.mark.parametrize("models", ["", " , , "])
async def test_combo_create_rejects_empty_models(client, models):
    response = await client.post("/dashboard/api/combos", data={"name": "empty", "models": models})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


async def test_combo_update_rejects_empty_models_and_missing_combo(client, app):
    created = await client.post(
        "/dashboard/api/combos", data={"name": "combo", "models": "provider/model"}
    )
    from janus.storage.combos_db import list_combos

    combo_id = (await list_combos(app.state.db_path))[0]["id"]
    empty_update = await client.put(
        f"/dashboard/api/combos/{combo_id}", data={"name": "combo", "models": ""}
    )
    missing_update = await client.put(
        "/dashboard/api/combos/999", data={"name": "combo", "models": "provider/model"}
    )

    assert created.status_code == 200
    assert empty_update.status_code == 422
    assert missing_update.status_code == 404


async def test_provider_rejects_invalid_quota_without_clearing_existing_configuration(client, app):
    created = await client.post(
        "/dashboard/api/providers",
        data=provider_form(quota_window="weekly", quota_limit="50", quota_metric="tokens"),
    )
    invalid_update = await client.put(
        "/dashboard/api/providers/validation-provider",
        data=provider_form(
            quota_window="monthly",
            quota_limit="",
            quota_metric="tokens",
        ),
    )
    unchanged = await get_provider(app.state.db_path, "validation-provider")
    preserved_update = await client.put(
        "/dashboard/api/providers/validation-provider",
        data=provider_form(),
    )
    preserved = await get_provider(app.state.db_path, "validation-provider")

    assert created.status_code == 200
    assert invalid_update.status_code == 422
    assert unchanged is not None
    assert (unchanged["quota_window"], unchanged["quota_limit"], unchanged["quota_metric"]) == (
        "weekly",
        50,
        "tokens",
    )
    assert preserved_update.status_code == 200
    assert preserved is not None
    assert (preserved["quota_window"], preserved["quota_limit"], preserved["quota_metric"]) == (
        "weekly",
        50,
        "tokens",
    )


@pytest.mark.parametrize(
    "quota",
    [
        {"quota_window": "weekly", "quota_limit": ""},
        {"quota_window": "", "quota_limit": "50"},
        {"quota_window": "weekly", "quota_limit": "50", "quota_metric": "invalid"},
    ],
)
async def test_provider_rejects_incomplete_or_invalid_quota_configuration(client, app, quota):
    response = await client.post("/dashboard/api/providers", data=provider_form(**quota))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert await get_provider(app.state.db_path, "validation-provider") is None


@pytest.mark.parametrize("default_model", ["x" * 301, "bad\x01model"])
async def test_provider_rejects_invalid_default_model_without_echoing_credentials(
    client, app, default_model
):
    response = await client.post(
        "/dashboard/api/providers", data=provider_form(default_model=default_model)
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert "provider-secret" not in response.text
    assert await get_provider(app.state.db_path, "validation-provider") is None


async def test_budget_omitted_warning_percentage_preserves_existing_value(client, app):
    await client.get("/dashboard/api/v2/state/settings")
    _, key = await create_key(app.state.db_path, "budget-key")
    await create_or_update_budget(
        app.state.db_path, key_id=int(key["id"]), daily_limit=5, warn_pct=73
    )

    response = await client.post(
        "/dashboard/api/budgets", data={"key_select": str(key["id"]), "daily_limit": "10"}
    )

    from janus.storage.budgets import get_budgets

    budget = (await get_budgets(app.state.db_path))[0]
    assert response.status_code == 200
    assert budget["daily_limit"] == 10
    assert budget["warn_pct"] == 73


async def test_provider_connection_driver_exception_returns_safe_502(client, monkeypatch):
    created = await client.post("/dashboard/api/providers", data=provider_form())

    def invalid_driver(_api_type: str):
        raise httpx.InvalidURL("credential-must-not-appear")

    monkeypatch.setattr("janus.providers.drivers.get_driver", invalid_driver)
    response = await client.post("/dashboard/api/providers/validation-provider/test")

    assert created.status_code == 200
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "Connection test failed", "ok": False}
    assert "credential-must-not-appear" not in response.text


async def test_pricing_mutation_errors_do_not_echo_submitted_values(client):
    response = await client.post(
        "/dashboard/api/pricing",
        data={"model": "model-secret", "input_per_mtok": "value-secret", "output_per_mtok": "1"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert "value-secret" not in response.text
    assert "model-secret" not in response.text
