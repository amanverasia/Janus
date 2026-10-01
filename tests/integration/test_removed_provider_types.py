from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.providers.drivers import supported_api_types
from janus.storage.database import init_db, seed_from_config
from janus.storage.providers_db import create_provider, get_provider, list_providers
from janus.storage.upstream_keys import create_upstream_key, get_upstream_key
from tests.fixtures.dashboard_auth import with_dashboard_auth


def _provider_row(provider_id: str, api_type: str, prefix: str) -> dict[str, Any]:
    return {
        "id": provider_id,
        "prefix": prefix,
        "api_type": api_type,
        "base_url": "https://provider.example/v1",
        "api_key": f"{provider_id}-secret",
        "models": ["m1"],
    }


def _config(tmp_path: Any, *providers: ProviderConfig) -> JanusConfig:
    return JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=list(providers),
    )


def test_cursor_api_type_is_not_supported() -> None:
    assert "cursor" not in supported_api_types()


async def test_init_db_disables_cursor_providers_and_revokes_mirrored_keys(tmp_path: Any) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(db_path, _provider_row("legacy-cursor", "cursor", "cursor"))
    await create_provider(db_path, _provider_row("keeper", "openai_compat", "keep"))
    mirrored = await create_upstream_key(
        db_path,
        provider_id="cursor",
        key_value="legacy-cursor-secret",
        source_node="gateway:legacy-cursor",
    )
    unrelated = await create_upstream_key(
        db_path,
        provider_id="keep",
        key_value="keeper-secret",
        source_node="gateway:keeper",
    )

    await init_db(db_path)
    await init_db(db_path)

    legacy = await get_provider(db_path, "legacy-cursor")
    keeper = await get_provider(db_path, "keeper")
    assert legacy is not None and not legacy["is_enabled"]
    assert keeper is not None and keeper["is_enabled"]
    mirrored_row = await get_upstream_key(db_path, str(mirrored["id"]))
    unrelated_row = await get_upstream_key(db_path, str(unrelated["id"]))
    assert mirrored_row is not None and mirrored_row["status"] == "revoked"
    assert unrelated_row is not None and unrelated_row["status"] != "revoked"


async def test_seed_skips_unsupported_api_types(tmp_path: Any) -> None:
    config = _config(
        tmp_path,
        ProviderConfig(
            id="cu", prefix="cu", api_type="cursor", base_url="https://c/v1", models=["m1"]
        ),
        ProviderConfig(
            id="ok", prefix="ok", api_type="openai_compat", base_url="https://o/v1", models=["m1"]
        ),
    )
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_from_config(db_path, config)

    assert {row["id"] for row in await list_providers(db_path)} == {"ok"}


async def test_reload_skips_enabled_row_with_unsupported_api_type(tmp_path: Any) -> None:
    app = create_app(config=_config(tmp_path))
    db_path = app.state.db_path
    await init_db(db_path)
    await create_provider(db_path, _provider_row("legacy-cursor", "cursor", "cursor"))
    await create_provider(db_path, _provider_row("keeper", "openai_compat", "keep"))

    await reload_providers(app)

    assert "keeper" in app.state.providers
    assert "legacy-cursor" not in app.state.providers
    assert app.state.registry.has_route("keep/m1")
    assert not app.state.registry.has_route("cursor/m1")


@pytest.mark.parametrize("api_type", ["cursor"])
async def test_dashboard_rejects_removed_api_type(tmp_path: Any, api_type: str) -> None:
    app = with_dashboard_auth(create_app(config=_config(tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/providers",
            data={
                "id": "removed",
                "prefix": "removed",
                "api_type": api_type,
                "base_url": "https://provider.example/v1",
                "api_key": "removed-secret",
                "models": "m1",
            },
        )

    assert response.status_code == 422
    assert "removed-secret" not in response.text
    assert await get_provider(app.state.db_path, "removed") is None
