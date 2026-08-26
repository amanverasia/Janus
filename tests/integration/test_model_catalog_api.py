from __future__ import annotations

import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.storage.api_keys import create_key

ADMIN_KEY = "model-admin-secret"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}", "Accept": "application/json"}
pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="test-provider",
                catalog_id="custom",
                prefix="test",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="provider-secret",
                models=["model-1", "model-2"],
                default_model="model-1",
            )
        ],
        api_keys=[ADMIN_KEY],
    )
    return create_app(config=config)


def remote_transport(app):
    return ASGITransport(app=app, client=("203.0.113.10", 4321))


async def test_management_catalog_is_safe_and_custom_models_have_first_class_crud(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        initial = await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)
        state = await client.get("/dashboard/api/v2/state/models", headers=ADMIN_HEADERS)
        presets = await client.get("/dashboard/api/v2/provider-presets", headers=ADMIN_HEADERS)
        created = await client.post(
            "/dashboard/api/v2/custom-models",
            headers=ADMIN_HEADERS,
            json={
                "provider_id": "test-provider",
                "model_id": "custom/model",
                "display_name": "Custom Model",
                "context_window": 64_000,
                "input_modalities": ["text", "image"],
                "reasoning_efforts": ["low", "high"],
                "capabilities": {"vision": True, "tool_use": True},
            },
        )
        custom_id = created.json()["custom_model"]["id"]
        updated = await client.put(
            f"/dashboard/api/v2/custom-models/{custom_id}",
            headers=ADMIN_HEADERS,
            json={"display_name": "Renamed Model", "max_output_tokens": 8_192},
        )
        models = await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)
        custom_list = await client.get("/dashboard/api/v2/custom-models", headers=ADMIN_HEADERS)
        ollama_tags = await client.get("/api/tags", headers=ADMIN_HEADERS)
        deleted = await client.delete(
            f"/dashboard/api/v2/custom-models/{custom_id}", headers=ADMIN_HEADERS
        )

    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "private, no-store"
    assert initial.json()["providers"] == [
        {
            "id": "test-provider",
            "catalog_id": "custom",
            "name": "Custom Provider",
            "prefix": "test",
            "is_enabled": True,
        }
    ]
    assert "provider-secret" not in json.dumps(initial.json())
    assert state.status_code == 200
    assert state.json()["data"] == initial.json()
    assert presets.status_code == 200
    assert "provider-secret" not in json.dumps(presets.json())
    assert any(preset["id"] == "openai" for preset in presets.json()["presets"])
    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["custom_model"]["display_name"] == "Renamed Model"
    custom_row = next(row for row in models.json()["models"] if row["source"] == "custom")
    assert custom_row["namespaced"] == "test/custom/model"
    assert custom_row["custom_id"] == custom_id
    assert custom_row["provider_id"] == "test-provider"
    assert custom_row["provider_enabled"] is True
    assert custom_row["custom_enabled"] is True
    assert custom_list.json()["custom_models"][0]["id"] == custom_id
    assert "test/custom/model" in {model["name"] for model in ollama_tags.json()["models"]}
    assert deleted.json() == {"deleted": True, "id": custom_id}


@respx.mock
async def test_selected_models_hide_discovery_but_do_not_block_direct_routing(app) -> None:
    respx.post("https://provider.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "response-1",
                "object": "chat.completion",
                "model": "model-1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "still routable"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)
        hidden = await client.put(
            "/dashboard/api/v2/model-visibility",
            headers=ADMIN_HEADERS,
            json={
                "scope": "models",
                "provider": "test-provider",
                "targets": [{"id": "model-1", "native": False}],
                "enabled": False,
            },
        )
        public_models = await client.get("/v1/models", headers=ADMIN_HEADERS)
        ollama_tags = await client.get("/api/tags", headers=ADMIN_HEADERS)
        routed = await client.post(
            "/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json={
                "model": "test/model-1",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert hidden.status_code == 200
    hidden_row = next(row for row in hidden.json()["models"] if row["id"] == "model-1")
    assert hidden_row["disabled"] is True
    assert hidden_row["selected"] is False
    assert "test/model-1" not in {row["id"] for row in public_models.json()["data"]}
    assert "test/model-2" in {row["id"] for row in public_models.json()["data"]}
    assert "test/model-1" not in {row["name"] for row in ollama_tags.json()["models"]}
    assert "test/model-2" in {row["name"] for row in ollama_tags.json()["models"]}
    assert routed.status_code == 200
    assert routed.json()["choices"][0]["message"]["content"] == "still routable"


async def test_gateway_catalog_is_scoped_but_dashboard_admin_catalog_is_global(app) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)
        scoped_key, _ = await create_key(
            app.state.db_path,
            "scoped-model-admin",
            can_login=True,
            allowed_models=["test/model-1"],
        )
        headers = {"Authorization": f"Bearer {scoped_key}", "Accept": "application/json"}
        management = await client.get("/dashboard/api/v2/models", headers=headers)
        public = await client.get("/v1/models", headers=headers)

    assert {row["namespaced"] for row in management.json()["models"]} == {
        "test/model-1",
        "test/model-2",
    }
    assert {row["id"] for row in public.json()["data"]} == {"test/model-1"}
    assert public.json()["data"][0]["owned_by"] == "test-provider"
    assert "::" not in public.json()["data"][0]["owned_by"]


async def test_public_model_catalog_reuses_reload_snapshot(app, monkeypatch) -> None:
    async with AsyncClient(transport=remote_transport(app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)

        async def fail_storage_read(*args, **kwargs):
            raise AssertionError("model catalog storage was read per request")

        monkeypatch.setattr("janus.models.catalog.list_catalog_models", fail_storage_read)
        first = await client.get("/v1/models", headers=ADMIN_HEADERS)
        second = await client.get("/v1/models", headers=ADMIN_HEADERS)

    assert first.status_code == 200
    assert second.json() == first.json()


async def test_public_catalog_aggregates_multiple_provider_rows_with_shared_prefix(
    tmp_path,
) -> None:
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="shared-a",
                catalog_id="shared",
                prefix="shared",
                api_type="openai_compat",
                base_url="https://a.example/v1",
                models=["model"],
                selected_models=["some-other-model"],
            ),
            ProviderConfig(
                id="shared-b",
                catalog_id="shared",
                prefix="shared",
                api_type="openai_compat",
                base_url="https://b.example/v1",
                models=["model"],
            ),
        ],
        api_keys=[ADMIN_KEY],
    )
    shared_app = create_app(config=config)
    async with AsyncClient(
        transport=remote_transport(shared_app), base_url="http://test"
    ) as client:
        await client.get("/dashboard/api/v2/models", headers=ADMIN_HEADERS)
        response = await client.get("/v1/models", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    rows = [row for row in response.json()["data"] if row["id"] == "shared/model"]
    assert rows == [{"id": "shared/model", "object": "model", "created": 0, "owned_by": "shared-b"}]
