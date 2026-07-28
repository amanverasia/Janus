import json

from janus.catalog import PROVIDERS
from janus.inventory.catalog import get_inventory_provider
from janus.inventory.codex_credentials import expand_codex_paste, normalize_codex_credential


def test_normalize_9router_connection_maps_workspace_and_expiry() -> None:
    raw = json.dumps(
        {
            "accessToken": "at-value",
            "refreshToken": "rt-value",
            "expiresAt": "2026-08-04T01:46:50.826Z",
            "idToken": "idt",
            "provider": "codex",
            "name": "user@example.com",
            "email": "user@example.com",
            "providerSpecificData": {
                "chatgptAccountId": "acct-uuid",
                "chatgptPlanType": "plus",
            },
        }
    )
    out = json.loads(normalize_codex_credential(raw))
    assert out["access_token"] == "at-value"
    assert out["refresh_token"] == "rt-value"
    assert out["extra"]["workspaceId"] == "acct-uuid"
    assert isinstance(out["expires_at"], (int, float))
    assert out["expires_at"] > 1_700_000_000


def test_normalize_janus_blob_passthrough_compact() -> None:
    raw = '{\n  "access_token": "a",\n  "refresh_token": "r",\n  "extra": {"workspaceId": "w"}\n}'
    out = normalize_codex_credential(raw)
    assert "\n" not in out
    assert json.loads(out)["extra"]["workspaceId"] == "w"


def test_normalize_bare_token() -> None:
    assert json.loads(normalize_codex_credential("eyJhbGciOi.bare.token")) == {
        "access_token": "eyJhbGciOi.bare.token"
    }


def test_expand_provider_connections_filters_non_codex() -> None:
    raw = json.dumps(
        {
            "providerConnections": [
                {"provider": "nvidia", "accessToken": "nv"},
                {
                    "provider": "codex",
                    "accessToken": "at",
                    "refreshToken": "rt",
                    "name": "c1@x.com",
                    "providerSpecificData": {"chatgptAccountId": "w1"},
                },
            ]
        }
    )
    entries = expand_codex_paste(raw)
    assert len(entries) == 1
    assert entries[0]["label"] == "c1@x.com"
    assert json.loads(entries[0]["key"])["access_token"] == "at"


def test_expand_array_of_connections() -> None:
    raw = json.dumps(
        [
            {
                "provider": "codex",
                "accessToken": "a1",
                "refreshToken": "r1",
                "email": "a@b.c",
            },
            {
                "provider": "codex",
                "accessToken": "a2",
                "refreshToken": "r2",
                "name": "n2",
            },
        ]
    )
    entries = expand_codex_paste(raw)
    assert len(entries) == 2
    assert entries[0]["label"] == "a@b.c"
    assert entries[1]["label"] == "n2"


def test_codex_has_inventory_and_gateway() -> None:
    assert "inventory" in PROVIDERS["codex"]
    inv = get_inventory_provider("codex")
    assert inv is not None
    assert inv["id"] == "codex"
    assert inv["display_name"] == "Codex (ChatGPT)"
    assert inv["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert inv["models_endpoint"] is None
    assert inv["billing_model"] == "subscription"
