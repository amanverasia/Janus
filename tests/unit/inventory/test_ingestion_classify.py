import json

import pytest

from janus.dashboard.inventory_routes import _parse_bulk_keys
from janus.inventory.ingestion import (
    UNSUPPORTED_FORMAT_ERROR,
    KeyIngestEntry,
    classify_upstream_entry,
    detect_credential_format,
    ingest_upstream_key,
)
from janus.storage.database import init_db, seed_inventory_providers
from janus.storage.upstream_keys import create_upstream_key, get_upstream_key


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_inventory_providers(db_path)
    return db_path


@pytest.mark.parametrize(
    ("value", "hint", "expected"),
    [
        ("gsk_" + "a" * 20, None, ("api_key", "groq")),
        ("plain-unknown-api-key-value", None, ("api_key", None)),
        ("bare-codex-access-token", "codex", ("api_key", "codex")),
        (
            json.dumps({"access_token": "a", "refresh_token": "b"}),
            "codex",
            ("codex_auth_json", "codex"),
        ),
        (json.dumps({"access_token": "a"}), "openai", ("unknown", "openai")),
        (json.dumps({"tokens": {"access_token": "a"}}), None, ("codex_auth_json", "codex")),
        (
            json.dumps({"provider": "chatgpt", "accessToken": "a"}),
            None,
            ("codex_auth_json", "codex"),
        ),
        (
            json.dumps({"access_token": "a", "extra": {"workspaceId": "w"}}),
            None,
            ("codex_auth_json", "codex"),
        ),
        (
            json.dumps({"access_token": "a", "project_id": "p"}),
            None,
            ("antigravity_json", "antigravity"),
        ),
        (json.dumps({"accessToken": "a", "authMethod": "social"}), None, ("oauth_json", "kiro")),
        (
            json.dumps({"access_token": "a", "expiry_date": 1, "scope": "s"}),
            None,
            ("unknown", None),
        ),
        (
            json.dumps({"claudeAiOauth": {"accessToken": "a"}}),
            None,
            ("oauth_json", "claude_oauth"),
        ),
        ("{not json", None, ("unknown", None)),
    ],
)
def test_detect_credential_format(value, hint, expected) -> None:
    assert detect_credential_format(value, provider_hint=hint) == expected


async def test_classify_reports_new_exists_and_rejected(db) -> None:
    stored = "sk-proj-classify-existing-value"
    await create_upstream_key(db, provider_id="openai", key_value=stored)

    new = await classify_upstream_entry(db, KeyIngestEntry(key="gsk_" + "n" * 20), "auto")
    exists = await classify_upstream_entry(db, KeyIngestEntry(key=stored), "groq")
    short = await classify_upstream_entry(db, KeyIngestEntry(key="tiny"), "auto")
    unknown = await classify_upstream_entry(db, KeyIngestEntry(key="gsk_" + "u" * 20), "nope")

    assert new.public() == {
        "key_masked": new.key_masked,
        "label": None,
        "provider_id": "groq",
        "provider_display_name": "Groq",
        "format": "api_key",
        "status": "new",
        "error": None,
    }
    assert (exists.status, exists.provider_id) == ("exists", "openai")
    assert short.status == "rejected" and "too short" in (short.error or "")
    assert unknown.status == "rejected" and unknown.error == "Unknown provider: nope"
    assert "key_value" not in new.public()


async def test_classify_treats_unidentified_row_as_new(db) -> None:
    key = "sk-proj-" + "q" * 20
    await create_upstream_key(db, provider_id="unidentified", key_value=key)

    result = await classify_upstream_entry(db, KeyIngestEntry(key=key), "openai")

    assert result.status == "new"
    assert result.provider_id == "openai"


async def test_ingest_rejects_unsupported_json_in_auto_mode(db) -> None:
    blob = json.dumps({"access_token": "ya29." + "g" * 30, "expiry_date": 1, "scope": "x"})

    result = await ingest_upstream_key(db, KeyIngestEntry(key=blob), chosen_provider="auto")

    assert result["status"] == "rejected"
    assert result["error"] == UNSUPPORTED_FORMAT_ERROR


async def test_ingest_auto_registers_codex_cli_auth_without_network(db) -> None:
    blob = json.dumps(
        {
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": "cli-access-token-long",
                "refresh_token": "cli-refresh-token-long",
                "account_id": "acct-9",
            },
        }
    )

    result = await ingest_upstream_key(db, KeyIngestEntry(key=blob), chosen_provider="auto")

    assert result["status"] == "registered"
    assert result["provider_id"] == "codex"
    row = await get_upstream_key(db, result["id"])
    assert row is not None
    stored = json.loads(row["key_value"])
    assert stored["access_token"] == "cli-access-token-long"
    assert stored["extra"] == {"workspaceId": "acct-9"}


async def test_ingest_antigravity_keeps_project_id(db) -> None:
    blob = json.dumps(
        {"access_token": "ag-access-token-long", "refresh_token": "ag-rt", "projectId": "proj-1"}
    )
    entries = _parse_bulk_keys(blob)

    result = await ingest_upstream_key(
        db, KeyIngestEntry(key=entries[0]["key"]), chosen_provider="auto"
    )

    assert result["status"] == "registered"
    assert result["provider_id"] == "antigravity"
    row = await get_upstream_key(db, result["id"])
    assert row is not None
    assert json.loads(row["key_value"])["extra"] == {"projectId": "proj-1"}


def test_parse_bulk_keys_keeps_single_blob_unmodified() -> None:
    blob = json.dumps({"accessToken": "kiro-at", "refreshToken": "kiro-rt", "profileArn": "arn"})

    assert _parse_bulk_keys(blob) == [{"label": "", "key": blob}]


def test_parse_bulk_keys_tags_expanded_codex_accounts() -> None:
    raw = json.dumps([{"accessToken": "one-long-token", "name": "a"}])

    entries = _parse_bulk_keys(raw)

    assert entries[0]["provider"] == "codex"
    assert entries[0]["label"] == "a"
