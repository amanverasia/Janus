import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.api_keys import create_key, list_keys
from janus.storage.budgets import create_or_update_budget, get_budget_status, get_budgets
from janus.storage.database import init_db
from janus.storage.settings import get_setting
from tests.fixtures.dashboard_auth import with_dashboard_auth


@pytest.fixture
async def budget_app(tmp_path):
    app = with_dashboard_auth(
        create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    )
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
async def test_invalid_budget_form_returns_structured_error_and_does_not_mutate(
    budget_app,
    data: dict[str, str],
    message: str,
) -> None:
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/dashboard/api/budgets", data=data)

    assert response.status_code == 422
    assert response.json() == {"detail": message}
    assert await get_budgets(app.state.db_path) == []


async def test_budget_state_exposes_reporting_day_boundary(budget_app) -> None:
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/api/v2/state/budgets")

    assert response.status_code == 200
    assert response.json()["data"]["reporting_timezone"] == "UTC"
    assert response.json()["data"]["keys"][0]["name"] == "budget-key"


@pytest.mark.parametrize("daily", ["", "2.5"])
async def test_budget_form_supports_absolute_only_and_combined_limits(budget_app, daily):
    app, key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/budgets",
            data={
                "key_select": str(key["id"]),
                "daily_limit": daily,
                "absolute_limit": "20",
                "warn_pct": "75",
            },
        )
        state = await client.get("/dashboard/api/v2/state/budgets")
    assert response.status_code == 200
    row = state.json()["data"]["budgets"][0]
    assert row["daily_limit"] == (2.5 if daily else None)
    assert row["absolute_limit"] == 20
    assert row["status"]["total_spend"] == 0
    assert row["status"]["absolute_remaining"] == 20
    assert row["status"]["absolute_status"] == "ok"


async def test_budget_form_omission_preserves_other_limit_and_blank_clears_it(budget_app):
    app, key = budget_app
    await create_or_update_budget(app.state.db_path, key_id=key["id"], absolute_limit=10)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/budgets", data={"key_select": str(key["id"]), "daily_limit": "5"}
        )
        assert response.status_code == 200
        row = (await get_budgets(app.state.db_path))[0]
        assert row["absolute_limit"] == 10
        assert row["daily_limit"] == 5
        response = await client.post(
            "/dashboard/api/budgets",
            data={"key_select": str(key["id"]), "absolute_limit": ""},
        )
        assert response.status_code == 200
        assert (await get_budgets(app.state.db_path))[0]["absolute_limit"] is None
        assert (await get_budgets(app.state.db_path))[0]["daily_limit"] == 5
        response = await client.post(
            "/dashboard/api/budgets", data={"key_select": str(key["id"]), "daily_limit": ""}
        )
    assert response.status_code == 422
    assert (await get_budgets(app.state.db_path))[0]["daily_limit"] == 5


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf", "nope"])
async def test_budget_form_rejects_invalid_absolute_limit_without_mutation(budget_app, value):
    app, key = budget_app
    await create_or_update_budget(app.state.db_path, key_id=key["id"], daily_limit=5)
    before = await get_budgets(app.state.db_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/budgets",
            data={"key_select": str(key["id"]), "daily_limit": "10", "absolute_limit": value},
        )
    assert response.status_code == 422
    assert "Absolute limit" in response.text
    assert await get_budgets(app.state.db_path) == before


@pytest.mark.parametrize(
    "data",
    [
        {"key_select": "global", "absolute_limit": "5"},
        {"key_select": "global", "daily_limit": "5", "absolute_limit": "10"},
        {"key_select": "global", "daily_limit": "", "absolute_limit": ""},
    ],
)
async def test_budget_form_requires_limits_and_specific_key_for_absolute(budget_app, data):
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/dashboard/api/budgets", data=data)
    assert response.status_code == 422
    assert await get_budgets(app.state.db_path) == []


@pytest.mark.parametrize("daily", ["", "5"])
async def test_key_create_supports_absolute_budget_and_safe_state(budget_app, daily):
    app, _key = budget_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/v2/keys",
            data={"name": "absolute-key", "daily_budget": daily, "absolute_budget": "25"},
        )
        state = await client.get("/dashboard/api/v2/state/keys")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    row = next(key for key in state.json()["data"]["keys"] if key["name"] == "absolute-key")
    assert row["budget"]["absolute_limit"] == 25
    assert row["budget"]["daily_limit"] == (5 if daily else None)
    assert response.json()["api_key"] not in state.text


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf", "nope"])
@pytest.mark.parametrize("creating", [True, False])
async def test_invalid_key_absolute_budget_is_validated_before_key_mutation(
    budget_app, value, creating
):
    app, key = budget_app
    before_keys = await list_keys(app.state.db_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/v2/keys" if creating else f"/dashboard/api/keys/{key['id']}",
            data={"name": "should-not-change", "daily_budget": "5", "absolute_budget": value},
        )
    assert response.status_code == 422
    assert "Absolute budget" in response.json()["detail"]
    assert await list_keys(app.state.db_path) == before_keys
    assert await get_budgets(app.state.db_path) == []


async def test_key_edit_preserves_or_explicitly_clears_each_budget(budget_app):
    app, key = budget_app
    db_path = app.state.db_path
    await create_or_update_budget(db_path, key_id=key["id"], daily_limit=5, warn_pct=70)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/dashboard/api/keys/{key['id']}", data={"absolute_budget": "10"}
        )
        assert response.status_code == 200
        status = await get_budget_status(db_path, key_id=key["id"])
        assert (status["daily_limit"], status["absolute_limit"], status["warn_pct"]) == (5, 10, 70)
        response = await client.post(
            f"/dashboard/api/keys/{key['id']}", data={"name": "renamed", "daily_budget": ""}
        )
        assert response.status_code == 200
        assert (await get_budget_status(db_path, key_id=key["id"]))["daily_limit"] == 5
        response = await client.post(
            f"/dashboard/api/keys/{key['id']}",
            data={"budget_fields": "1", "daily_budget": "", "absolute_budget": "10"},
        )
        assert response.status_code == 200
        status = await get_budget_status(db_path, key_id=key["id"])
        assert status["daily_limit"] is None
        assert status["absolute_limit"] == 10
        response = await client.post(
            f"/dashboard/api/keys/{key['id']}",
            data={"budget_fields": "1", "daily_budget": "", "absolute_budget": ""},
        )
        assert response.status_code == 200
        assert await get_budgets(db_path) == []
        missing = await client.post("/dashboard/api/keys/999999", data={"absolute_budget": "5"})
        assert missing.status_code == 404
        assert await get_budgets(db_path) == []


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
