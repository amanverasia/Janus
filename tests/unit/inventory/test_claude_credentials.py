from __future__ import annotations

import json
from pathlib import Path

import pytest

from janus.catalog import prefix_to_inventory_map
from janus.inventory.claude_credentials import normalize_claude_credential
from janus.inventory.ingestion import detect_credential_format
from janus.inventory.url_guard import detect_provider_from_key


def test_normalize_claude_code_file() -> None:
    raw = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-AAAA",
                "refreshToken": "sk-ant-ort01-BBBB",
                "expiresAt": 1790000000000,
                "scopes": ["user:inference"],
                "subscriptionType": "max",
                "email": "person@example.com",
            }
        }
    )
    out = json.loads(normalize_claude_credential(raw))
    assert out == {
        "access_token": "sk-ant-oat01-AAAA",
        "refresh_token": "sk-ant-ort01-BBBB",
        "expires_at": 1790000000.0,
        "extra": {"subscriptionType": "max"},
    }


def test_normalize_flat_json_and_bare_token() -> None:
    flat = json.loads(
        normalize_claude_credential('{"access_token": "sk-ant-oat01-X", "refresh_token": "r"}')
    )
    assert flat == {"access_token": "sk-ant-oat01-X", "refresh_token": "r"}
    bare = json.loads(normalize_claude_credential("  sk-ant-oat01-BARE  "))
    assert bare == {"access_token": "sk-ant-oat01-BARE"}


@pytest.mark.parametrize("raw", ["", "{}", '{"claudeAiOauth": {}}', "[1]"])
def test_normalize_rejects_missing_access_token(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_claude_credential(raw)


def test_detect_provider_from_key_claude_oauth() -> None:
    assert detect_provider_from_key("sk-ant-oat01-abcdef") == "claude_oauth"
    assert detect_provider_from_key("sk-ant-api03-abcdef") == "anthropic"


def test_claude_code_file_detected_as_claude_oauth() -> None:
    raw = json.dumps({"claudeAiOauth": {"accessToken": "a"}})
    assert detect_credential_format(raw) == ("oauth_json", "claude_oauth")


def test_claude_prefix_maps_to_inventory() -> None:
    assert prefix_to_inventory_map()["claude"] == "claude_oauth"


async def test_migration_repoints_mirrored_claude_keys(tmp_path: Path) -> None:
    from janus.inventory.provider_key_sync import _upsert_custom_inventory_provider
    from janus.storage.database import init_db
    from janus.storage.providers_db import create_provider
    from janus.storage.upstream_keys import create_upstream_key, get_upstream_key

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    row = {
        "id": "cl",
        "prefix": "claude",
        "api_type": "claude_oauth",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-oat01-x",
        "models": ["m"],
    }
    await create_provider(db_path, row)
    await _upsert_custom_inventory_provider(db_path, row, "claude")
    mirrored = await create_upstream_key(
        db_path, provider_id="claude", key_value="sk-ant-oat01-x", source_node="gateway:cl"
    )
    other = await create_upstream_key(db_path, provider_id="claude", key_value="sk-other-key-1")

    await init_db(db_path)
    await init_db(db_path)

    moved = await get_upstream_key(db_path, str(mirrored["id"]))
    untouched = await get_upstream_key(db_path, str(other["id"]))
    assert moved is not None and moved["provider_id"] == "claude_oauth"
    assert untouched is not None and untouched["provider_id"] == "claude"
