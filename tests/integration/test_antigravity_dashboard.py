import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings


@pytest.fixture
async def app(tmp_path):
    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    return app


@pytest.mark.asyncio
@respx.mock
async def test_fetch_models_antigravity(app):
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    respx.post("https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist").mock(
        return_value=httpx.Response(
            200,
            json={"cloudaicompanionProject": {"id": "project-1"}},
        )
    )
    respx.post("https://cloudcode-pa.googleapis.com/v1internal:onboardUser").mock(
        return_value=httpx.Response(200, json={"done": True})
    )
    respx.post("https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": {
                    "gemini-2.5-flash": {},
                    "internal-model": {"isInternal": True},
                }
            },
        )
    )
    credential = json.dumps({"access_token": "access-token"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/dashboard/api/providers/fetch-models",
            data={
                "api_type": "antigravity",
                "base_url": "https://daily-cloudcode-pa.googleapis.com",
                "api_key": credential,
            },
        )
    assert response.status_code == 200
    assert response.json() == {"models": ["gemini-2.5-flash"]}
