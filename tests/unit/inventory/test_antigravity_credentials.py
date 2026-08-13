import json

import pytest

from janus.inventory.antigravity_credentials import normalize_antigravity_credential


def test_normalize_antigravity_camel_case_credential() -> None:
    result = json.loads(
        normalize_antigravity_credential(
            json.dumps(
                {
                    "accessToken": "access",
                    "refreshToken": "refresh",
                    "expiresAt": "2030-01-01T00:00:00Z",
                    "providerSpecificData": {"projectId": "project-1"},
                }
            )
        )
    )
    assert result["access_token"] == "access"
    assert result["refresh_token"] == "refresh"
    assert result["extra"] == {"projectId": "project-1"}
    assert isinstance(result["expires_at"], float)


def test_normalize_antigravity_bare_token() -> None:
    assert json.loads(normalize_antigravity_credential("token")) == {"access_token": "token"}


def test_normalize_antigravity_requires_access_token() -> None:
    with pytest.raises(ValueError, match="missing access token"):
        normalize_antigravity_credential(json.dumps({"refresh_token": "refresh"}))
