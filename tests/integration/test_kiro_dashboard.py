import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
async def app(tmp_path):
    return create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_models_kiro_uses_live_account_catalog(app):
    route = respx.get("https://q.eu-west-1.amazonaws.com/ListAvailableModels").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"modelId": "claude-sonnet-4.5", "modelName": "Claude Sonnet 4.5"},
                    {"id": "auto", "modelName": "Auto"},
                    {"modelId": "claude-sonnet-4.5"},
                ]
            },
        )
    )
    credential = json.dumps(
        {
            "accessToken": "access-token",
            "authMethod": "google",
            "extra": {"profileArn": "arn:aws:codewhisperer:eu-west-1:123456789012:profile/test"},
        }
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/providers/fetch-models",
            data={
                "api_type": "kiro",
                "base_url": "https://runtime.us-east-1.kiro.dev",
                "api_key": credential,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["auto", "claude-sonnet-4.5"]}
    request = route.calls.last.request
    assert request.url.params["origin"] == "AI_EDITOR"
    assert request.url.params["profileArn"].startswith("arn:aws:codewhisperer:eu-west-1:")
    assert request.headers["Authorization"] == "Bearer access-token"
    assert request.headers["x-amzn-kiro-agent-mode"] == "vibe"
    assert "KiroIDE-0.10.32-" in request.headers["User-Agent"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_models_kiro_reports_upstream_authorization_error(app):
    respx.get("https://q.us-east-1.amazonaws.com/ListAvailableModels").mock(
        return_value=httpx.Response(
            403,
            json={"message": "User is not authorized to make this call.", "reason": None},
        )
    )
    credential = json.dumps({"accessToken": "access-token", "authMethod": "builder-id"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/providers/fetch-models",
            data={
                "api_type": "kiro",
                "base_url": "https://runtime.us-east-1.kiro.dev",
                "api_key": credential,
            },
        )
    assert response.status_code == 502
    assert response.json()["error"] == (
        "Kiro ListAvailableModels returned 403: User is not authorized to make this call."
    )


@pytest.mark.asyncio
async def test_fetch_models_kiro_requires_credential(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/providers/fetch-models",
            data={
                "api_type": "kiro",
                "base_url": "https://runtime.us-east-1.kiro.dev",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"] == "No Kiro credential available"
