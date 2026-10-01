from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.catalog import PROVIDERS
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.storage.providers_db import toggle_provider
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

pytestmark = pytest.mark.asyncio

URL = "/dashboard/api/v2/state/models-unreachable"
HEADERS = {"Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}", "Accept": "application/json"}
OPENAI_KEY = "sk-openai-super-secret-value"
ANTHROPIC_KEY = "sk-ant-super-secret-value"
OPENAI_BASE = "https://openai.internal-example.test/v1"
ANTHROPIC_BASE = "https://anthropic.internal-example.test"


@pytest.fixture
async def app(tmp_path: Any) -> FastAPI:
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="openai-main",
                catalog_id="openai",
                prefix="openai",
                api_type="openai_compat",
                base_url=OPENAI_BASE,
                api_key=OPENAI_KEY,
                models=["gpt-4o"],
                allowed_models=["gpt-4o"],
            ),
            ProviderConfig(
                id="anthropic-main",
                catalog_id="anthropic",
                prefix="anthropic",
                api_type="anthropic",
                base_url=ANTHROPIC_BASE,
                api_key=ANTHROPIC_KEY,
                models=[],
            ),
        ],
    )
    application = with_dashboard_auth(create_app(config=config))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        await client.get("/dashboard/api/v2/state/overview", headers=HEADERS)
    db_path = application.state.db_path
    await toggle_provider(db_path, "anthropic-main")
    await reload_providers(application)
    return application


async def _get(app: FastAPI, query: str = "") -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(URL + query, headers=HEADERS)


def _group(data: dict[str, Any], prefix: str) -> dict[str, Any]:
    matches = [group for group in data["groups"] if group["prefix"] == prefix]
    assert len(matches) == 1, prefix
    return matches[0]


async def test_section_lists_groups_and_reasons(app: FastAPI) -> None:
    response = await _get(app, "?limit=200")
    assert response.status_code == 200
    data = response.json()["data"]
    anthropic_defaults = PROVIDERS["anthropic"]["gateway"]["default_models"]
    anthropic = _group(data, "anthropic")
    assert anthropic["reasons"] == {"provider_disabled": len(anthropic_defaults)}
    assert anthropic["name"] == "Anthropic"
    assert anthropic["count"] == len(anthropic_defaults)
    assert len(anthropic["sample_models"]) <= 5
    openai = _group(data, "openai")
    assert set(openai["reasons"]) == {"model_not_enabled"}
    rows = {(row["prefix"], row["model"]): row for row in data["models"]}
    assert ("openai", "gpt-4o") not in rows
    assert any(prefix == "openai" for prefix, _ in rows)
    deepseek = _group(data, "deepseek")
    assert set(deepseek["reasons"]) == {"no_provider"}
    assert data["reachable_total"] >= 1
    assert data["unreachable_total"] == sum(group["count"] for group in data["groups"])
    counts = [(-group["count"], group["prefix"]) for group in data["groups"]]
    assert counts == sorted(counts)


async def test_connect_hrefs(app: FastAPI) -> None:
    data = (await _get(app)).json()["data"]
    assert _group(data, "anthropic")["connect"] == {
        "kind": "connect",
        "href": "/dashboard/ui/connect?provider=anthropic",
    }
    gateway_only = [
        catalog_id
        for catalog_id, entry in PROVIDERS.items()
        if "inventory" not in entry and entry.get("gateway", {}).get("default_models")
    ]
    assert gateway_only
    gateway_groups = [group for group in data["groups"] if group["catalog_id"] in gateway_only]
    assert gateway_groups
    for group in gateway_groups:
        assert group["connect"] == {"kind": "providers", "href": "/dashboard/ui/providers"}


async def test_filters_and_pagination(app: FastAPI) -> None:
    full = (await _get(app, "?limit=200")).json()
    no_provider_total = sum(
        group["reasons"].get("no_provider", 0) for group in full["data"]["groups"]
    )
    response = await _get(app, "?reason=no_provider&limit=2&offset=0")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["data"]["models"]) == 2
    assert all(row["reason"] == "no_provider" for row in payload["data"]["models"])
    assert payload["meta"]["pagination"]["total"] == no_provider_total
    assert payload["meta"]["pagination"]["limit"] == 2
    assert payload["meta"]["pagination"]["page"] == 1
    assert payload["meta"]["query"] == {"provider": "", "reason": "no_provider", "search": ""}

    anthropic = (await _get(app, "?provider=anthropic")).json()["data"]["models"]
    assert anthropic
    assert all(row["prefix"] == "anthropic" for row in anthropic)

    claude = (await _get(app, "?search=CLAUDE&limit=200")).json()["data"]["models"]
    assert claude
    assert all("claude" in row["model"].lower() for row in claude)


async def test_offset_past_end(app: FastAPI) -> None:
    full = (await _get(app)).json()
    response = await _get(app, "?offset=100000")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["models"] == []
    assert payload["data"]["unreachable_total"] == full["data"]["unreachable_total"]
    assert payload["meta"]["pagination"]["total"] == full["meta"]["pagination"]["total"]


async def test_invalid_reason_422(app: FastAPI) -> None:
    response = await _get(app, "?reason=not_in_allowlist")
    assert response.status_code == 422
    assert "reason" in response.json()["detail"]


async def test_invalid_provider_422(app: FastAPI) -> None:
    response = await _get(app, "?provider=no-such-prefix")
    assert response.status_code == 422
    assert "provider" in response.json()["detail"]


async def test_soon_lists_cooled_down(app: FastAPI) -> None:
    assert (await _get(app)).json()["data"]["soon"] == []
    app.state.provider_snapshot.handler.mark_cooldown("openai-main", "rate_limit", model="gpt-4o")
    data = (await _get(app, "?limit=200")).json()["data"]
    assert data["soon"] == [{"model": "gpt-4o", "prefix": "openai"}]
    assert ("openai", "gpt-4o") not in {(row["prefix"], row["model"]) for row in data["models"]}


async def test_report_cached_per_snapshot(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    import janus.dashboard.reachability_cache as cache

    original = cache.list_providers
    calls: list[int] = []

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(cache, "list_providers", counting)
    first = (await _get(app)).json()["data"]
    second = (await _get(app)).json()["data"]
    assert len(calls) == 1
    assert first["groups"] == second["groups"]
    assert _group(first, "anthropic")["reasons"] == {
        "provider_disabled": _group(first, "anthropic")["count"]
    }

    db_path = app.state.db_path
    await toggle_provider(db_path, "anthropic-main")
    await reload_providers(app)
    third = (await _get(app)).json()["data"]
    assert len(calls) == 2
    assert all(group["prefix"] != "anthropic" for group in third["groups"])


async def test_no_secrets_in_payload(app: FastAPI) -> None:
    app.state.provider_snapshot.handler.mark_cooldown("openai-main", "rate_limit", model="gpt-4o")
    text = (await _get(app, "?limit=200")).text
    for needle in (OPENAI_KEY, ANTHROPIC_KEY, OPENAI_BASE, ANTHROPIC_BASE, "account_id"):
        assert needle not in text
