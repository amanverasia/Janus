import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.api_keys import create_key
from janus.storage.budgets import get_budgets
from janus.storage.database import init_db
from janus.storage.settings import get_setting


@pytest.fixture
async def budget_app(tmp_path):
    app = create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    await init_db(app.state.db_path)
    _, key = await create_key(app.state.db_path, "budget-key")
    return app, key


async def test_budget_form_accepts_global_and_existing_key(budget_app) -> None:
    app, key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        global_response = await client.post(
            "/dashboard/api/budgets",
            data={"key_select": "global", "daily_limit": "5", "warn_pct": "80"},
        )
        key_response = await client.post(
            "/dashboard/api/budgets",
            data={"key_select": str(key["id"]), "daily_limit": "2.5", "warn_pct": "100"},
        )

    assert global_response.status_code == 200
    assert key_response.status_code == 200
    budgets = await get_budgets(app.state.db_path)
    assert [(budget["key_id"], budget["daily_limit"]) for budget in budgets] == [
        (None, 5.0),
        (key["id"], 2.5),
    ]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"key_select": "", "daily_limit": "5", "warn_pct": "80"},
            "Select a valid budget scope.",
        ),
        (
            {"key_select": "abc", "daily_limit": "5", "warn_pct": "80"},
            "Select a valid budget scope.",
        ),
        (
            {"key_select": "-1", "daily_limit": "5", "warn_pct": "80"},
            "Select a valid budget scope.",
        ),
        (
            {"key_select": "999999", "daily_limit": "5", "warn_pct": "80"},
            "The selected API key does not exist.",
        ),
        (
            {"key_select": "global", "daily_limit": "nope", "warn_pct": "80"},
            "Daily limit must be a number greater than zero.",
        ),
        (
            {"key_select": "global", "daily_limit": "0", "warn_pct": "80"},
            "Daily limit must be a number greater than zero.",
        ),
        (
            {"key_select": "global", "daily_limit": "-1", "warn_pct": "80"},
            "Daily limit must be a number greater than zero.",
        ),
        (
            {"key_select": "global", "daily_limit": "nan", "warn_pct": "80"},
            "Daily limit must be a number greater than zero.",
        ),
        (
            {"key_select": "global", "daily_limit": "5", "warn_pct": "0"},
            "Warning percentage must be between 1 and 100.",
        ),
        (
            {"key_select": "global", "daily_limit": "5", "warn_pct": "101"},
            "Warning percentage must be between 1 and 100.",
        ),
        (
            {"key_select": "global", "daily_limit": "5", "warn_pct": "nan"},
            "Warning percentage must be between 1 and 100.",
        ),
    ],
)
async def test_invalid_budget_form_is_htmx_friendly_and_does_not_mutate(
    budget_app,
    data: dict[str, str],
    message: str,
) -> None:
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/dashboard/api/budgets", data=data)

    assert response.status_code == 422
    assert response.text == message
    assert await get_budgets(app.state.db_path) == []


async def test_budget_page_wires_validation_errors_to_toast(budget_app) -> None:
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/budgets")

    assert response.status_code == 200
    assert "htmx:response-error" in response.text
    assert "xhr.responseText" in response.text
    assert "Daily periods reset at midnight in" in response.text


async def test_reporting_timezone_setting_validates_iana_name(budget_app) -> None:
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        valid = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_reporting_timezone", "value": " Asia/Kolkata "},
        )
        invalid = await client.post(
            "/dashboard/api/settings",
            data={"key": "server_reporting_timezone", "value": "Mars/Olympus_Mons"},
        )

    assert valid.status_code == 200
    assert invalid.status_code == 400
    assert "valid IANA timezone" in invalid.text
    assert await get_setting(app.state.db_path, "server_reporting_timezone") == "Asia/Kolkata"
