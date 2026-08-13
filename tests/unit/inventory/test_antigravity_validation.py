import json

import httpx
import pytest
import respx

from janus.inventory.key_checker import (
    ANTIGRAVITY_LOAD_CODE_ASSIST_URL,
    ANTIGRAVITY_ONBOARD_URL,
    validate_key,
)


@pytest.mark.asyncio
@respx.mock
async def test_antigravity_validation_discovers_project_and_onboards() -> None:
    load = respx.post(ANTIGRAVITY_LOAD_CODE_ASSIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "cloudaicompanionProject": {"id": "project-1"},
                "allowedTiers": [{"id": "free-tier", "isDefault": True}],
            },
        )
    )
    onboard = respx.post(ANTIGRAVITY_ONBOARD_URL).mock(
        return_value=httpx.Response(200, json={"done": True})
    )
    result = await validate_key(
        json.dumps(
            {
                "accessToken": "access-token",
                "providerSpecificData": {"projectId": "old-project"},
            }
        ),
        "antigravity",
    )
    assert result["is_valid"] is True
    assert result["is_usable"] is True
    assert result["usability_status"] == "usable"
    assert json.loads(result["key_value"])["extra"]["projectId"] == "project-1"
    assert load.called
    assert onboard.called
    assert load.calls[0].request.headers["User-Agent"] == "antigravity/ide/2.1.1 darwin/arm64"
    assert json.loads(load.calls[0].request.content)["metadata"] == {
        "ideType": 9,
        "platform": 3,
        "pluginType": 2,
    }


@pytest.mark.asyncio
@respx.mock
async def test_antigravity_auth_failure_is_invalid() -> None:
    respx.post(ANTIGRAVITY_LOAD_CODE_ASSIST_URL).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    result = await validate_key(json.dumps({"access_token": "access-token"}), "antigravity")
    assert result["is_valid"] is False
