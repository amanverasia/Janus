import json
import sqlite3
from typing import Any

import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.storage.upstream_keys import create_upstream_key, list_upstream_keys
from tests.fixtures.dashboard_auth import with_dashboard_auth

PREVIEW_URL = "/dashboard/api/inventory/preview"

CODEX_AT_1 = "codex-access-token-one-SECRET"
CODEX_RT_1 = "codex-refresh-token-one-SECRET"
CODEX_AT_2 = "codex-access-token-two-SECRET"
GEMINI_AT = "ya29.gemini-access-token-SECRET"
GEMINI_RT = "1//gemini-refresh-token-SECRET"


def _codex_connections() -> str:
    return json.dumps(
        {
            "providerConnections": [
                {
                    "provider": "codex",
                    "accessToken": CODEX_AT_1,
                    "refreshToken": CODEX_RT_1,
                    "name": "work",
                    "providerSpecificData": {"chatgptAccountId": "acct-1"},
                },
                {
                    "provider": "codex",
                    "accessToken": CODEX_AT_2,
                    "email": "me@example.com",
                },
                {"provider": "nvidia", "accessToken": "nvapi-ignored-connection"},
            ]
        }
    )


def _gemini_oauth_creds() -> str:
    return json.dumps(
        {
            "access_token": GEMINI_AT,
            "refresh_token": GEMINI_RT,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "token_type": "Bearer",
            "expiry_date": 1759000000000,
        }
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    monkeypatch.setattr(
        "janus.dashboard.inventory_routes._schedule_recheck",
        lambda key_id, db_path: None,
    )
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return with_dashboard_auth(create_app(config=cfg))


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/dashboard/api/v2/state/inventory")
        yield c


async def _preview(client: AsyncClient, keys_text: str, provider_id: str = "auto") -> Any:
    return await client.post(
        PREVIEW_URL,
        data={"keys_text": keys_text, "provider_id": provider_id},
        headers={"Accept": "application/json"},
    )


def _db_dump(db_path: Any) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


async def test_preview_codex_multi_account_counts_and_masks(client):
    response = await _preview(client, _codex_connections())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["ok"] is True
    assert payload["processed_count"] == 2
    assert payload["new_count"] == 2
    assert payload["exists_count"] == 0
    assert payload["rejected_count"] == 0
    assert payload["by_provider"] == [
        {"provider_id": "codex", "provider_display_name": "Codex (ChatGPT)", "count": 2}
    ]
    assert [item["label"] for item in payload["results"]] == ["work", "me@example.com"]
    for item in payload["results"]:
        assert set(item) == {
            "key_masked",
            "label",
            "provider_id",
            "provider_display_name",
            "format",
            "status",
            "error",
        }
        assert item["format"] == "codex_auth_json"
        assert item["provider_id"] == "codex"
        assert item["status"] == "new"
        assert item["error"] is None
    for secret in (CODEX_AT_1, CODEX_RT_1, CODEX_AT_2, "nvapi-ignored-connection"):
        assert secret not in response.text


async def test_preview_counts_new_duplicate_and_unsupported(client, app):
    existing = "sk-proj-already-stored-SECRET-value"
    await create_upstream_key(app.state.db_path, provider_id="openai", key_value=existing)
    groq = "gsk_preview-groq-SECRET-value"
    keys_text = "\n".join([groq, existing, _gemini_oauth_creds(), groq, "# comment"])

    response = await _preview(client, keys_text)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["processed_count"] == 4
    assert payload["new_count"] == 1
    assert payload["exists_count"] == 2
    assert payload["rejected_count"] == 1
    assert payload["by_provider"] == [
        {"provider_id": "groq", "provider_display_name": "Groq", "count": 1}
    ]
    new, stored, gemini, repeated = payload["results"]
    assert (new["status"], new["provider_id"], new["format"]) == ("new", "groq", "api_key")
    assert (stored["status"], stored["provider_id"]) == ("exists", "openai")
    assert repeated["status"] == "exists"
    assert gemini == {
        "key_masked": "****",
        "label": "",
        "provider_id": None,
        "provider_display_name": None,
        "format": "unknown",
        "status": "rejected",
        "error": "Unsupported credential format",
    }
    for secret in (existing, groq, GEMINI_AT, GEMINI_RT):
        assert secret not in response.text


async def test_preview_never_writes_to_database(client, app):
    before = _db_dump(app.state.db_path)
    keys_text = "\n".join(["gsk_no-write-SECRET-value-1", "sk-ant-no-write-SECRET-value"])
    response = await _preview(client, keys_text)
    codex = await _preview(client, _codex_connections())

    assert response.status_code == 200
    assert codex.status_code == 200
    assert _db_dump(app.state.db_path) == before
    assert await list_upstream_keys(app.state.db_path) == []


async def test_preview_makes_no_network_calls(client):
    with respx.mock(assert_all_called=False) as router:
        response = await _preview(client, "unknownprefix-plain-api-key-value")
        assert not router.calls

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["status"] == "new"
    assert item["provider_id"] is None
    assert response.json()["by_provider"][0]["provider_id"] == "unidentified"


@pytest.mark.parametrize(
    ("blob", "expected_format", "expected_provider"),
    [
        (
            {
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": "codex-cli-id-token-SECRET",
                    "access_token": "codex-cli-access-token-SECRET",
                    "refresh_token": "codex-cli-refresh-token-SECRET",
                    "account_id": "acct-cli",
                },
                "last_refresh": "2026-09-01T00:00:00Z",
            },
            "codex_auth_json",
            "codex",
        ),
        (
            {
                "access_token": "antigravity-access-token-SECRET",
                "refresh_token": "antigravity-refresh-token-SECRET",
                "projectId": "my-project",
            },
            "antigravity_json",
            "antigravity",
        ),
        (
            {
                "accessToken": "kiro-access-token-SECRET",
                "refreshToken": "kiro-refresh-token-SECRET",
                "profileArn": "arn:aws:codewhisperer:us-east-1:1:profile/x",
            },
            "oauth_json",
            "kiro",
        ),
    ],
)
async def test_preview_detects_oauth_credential_files(
    client, blob, expected_format, expected_provider
):
    response = await _preview(client, json.dumps(blob, indent=2))

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["status"] == "new"
    assert item["format"] == expected_format
    assert item["provider_id"] == expected_provider
    assert "SECRET" not in response.text.replace(item["key_masked"], "")


async def test_preview_matches_submit_for_codex_cli_auth(client, app):
    blob = json.dumps(
        {
            "tokens": {
                "access_token": "codex-cli-match-access-SECRET",
                "refresh_token": "codex-cli-match-refresh-SECRET",
                "account_id": "acct-match",
            }
        }
    )
    preview = (await _preview(client, blob)).json()["results"][0]
    submitted = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": blob, "provider_id": "auto"},
        headers={"Accept": "application/json"},
    )

    assert submitted.status_code == 200
    result = submitted.json()["results"][0]
    assert result["provider_id"] == preview["provider_id"] == "codex"
    assert result["key_masked"] == preview["key_masked"]
    again = (await _preview(client, blob)).json()
    assert again["results"][0]["status"] == "exists"
    assert again["exists_count"] == 1
    assert again["by_provider"] == []


async def test_preview_rejects_unsupported_json_on_submit_too(client, app):
    response = await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": _gemini_oauth_creds(), "provider_id": "auto"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["results"][0]["error"] == "Unsupported credential format"
    assert await list_upstream_keys(app.state.db_path) == []


async def test_preview_input_errors(client, monkeypatch):
    empty = await _preview(client, "  \n# only a comment\n")
    assert empty.status_code == 422
    assert empty.json() == {"ok": False, "error": "No credentials found in input."}
    assert empty.headers["cache-control"] == "no-store"

    too_large = await _preview(client, "x" * (1024 * 1024 + 1))
    assert too_large.status_code == 422
    assert too_large.json()["ok"] is False
    assert "too large" in too_large.json()["error"]

    oversized_upload = await client.post(
        PREVIEW_URL,
        files={"keys_text": (None, "y" * (5 * 1024 * 1024))},
    )
    assert oversized_upload.status_code == 413
    assert "too large" in oversized_upload.json()["error"]

    multipart = await client.post(
        PREVIEW_URL,
        files={"keys_text": (None, "gsk_multipart-preview-value"), "provider_id": (None, "auto")},
    )
    assert multipart.status_code == 200
    assert multipart.json()["new_count"] == 1

    missing = await client.post(PREVIEW_URL, data={"provider_id": "auto"})
    assert missing.status_code == 422
    assert missing.json() == {"ok": False, "error": "keys_text is required."}

    monkeypatch.setattr("janus.inventory.ingestion.MAX_SUBMIT_BATCH", 2)
    batch = await _preview(client, "\n".join(f"gsk_batch-key-value-{i:04d}" for i in range(3)))
    assert batch.status_code == 422
    assert "Too many keys" in batch.json()["error"]


async def test_preview_rate_limited(client, monkeypatch):
    class DenyLimiter:
        limit = 1

        def allow(self, client_id, cost):
            del client_id, cost
            return False

    monkeypatch.setattr(
        "janus.dashboard.inventory_routes.get_preview_rate_limiter", lambda: DenyLimiter()
    )
    secret = "sk-proj-rate-limit-preview-SECRET"
    response = await _preview(client, secret)

    assert response.status_code == 429
    assert response.json()["ok"] is False
    assert secret not in response.text


async def _submit(client: AsyncClient, keys_text: str) -> Any:
    return await client.post(
        "/dashboard/api/inventory/submit",
        data={"keys_text": keys_text, "provider_id": "auto"},
        headers={"Accept": "application/json"},
    )


async def test_preview_matches_submit_for_prefix_keys_even_if_probe_disagrees(client, monkeypatch):
    probed: list[str] = []

    async def fake_probe(key_value, candidate_ids, *, metadata=None):
        probed.append(key_value)
        return "venice"

    monkeypatch.setattr(
        "janus.inventory.provider_detection.find_authenticating_provider", fake_probe
    )
    keys = ["gsk_" + "d" * 40, "nvapi-" + "e" * 40, "sk-ant-" + "f" * 40]
    with respx.mock(assert_all_called=False) as router:
        preview = (await _preview(client, "\n".join(keys))).json()["results"]
        submitted = (await _submit(client, "\n".join(keys))).json()["results"]
        assert not router.calls

    assert probed == []
    assert [row["provider_id"] for row in preview] == ["groq", "nvidia", "anthropic"]
    assert [row["provider_id"] for row in submitted] == ["groq", "nvidia", "anthropic"]


async def test_generic_sk_key_is_probed_on_submit_not_guessed_in_preview(client, monkeypatch):
    async def fake_probe(key_value, candidate_ids, *, metadata=None):
        return "deepseek"

    monkeypatch.setattr(
        "janus.inventory.provider_detection.find_authenticating_provider", fake_probe
    )
    key = "sk-" + "g" * 40
    preview = (await _preview(client, key)).json()["results"][0]
    submitted = (await _submit(client, key)).json()["results"][0]

    assert (preview["status"], preview["provider_id"]) == ("new", None)
    assert submitted["provider_id"] == "deepseek"


CLINE_AT = "eyJcline-access-token-SECRET-value"
CLINE_RT = "cline-refresh-token-SECRET-value"


async def test_cline_export_previews_and_imports_with_refresh_metadata(client, app):
    blob = json.dumps(
        {
            "provider": "cline",
            "accessToken": CLINE_AT,
            "refreshToken": CLINE_RT,
            "email": "dev@example.com",
        }
    )
    preview = (await _preview(client, blob)).json()
    submitted = await _submit(client, blob)

    item = preview["results"][0]
    assert (item["status"], item["provider_id"], item["format"]) == ("new", "cline", "oauth_json")
    assert item["label"] == "dev@example.com"
    assert submitted.status_code == 200
    assert submitted.json()["results"][0]["provider_id"] == "cline"
    for body in (json.dumps(preview), submitted.text):
        assert CLINE_AT not in body
        assert CLINE_RT not in body
    (stored,) = await list_upstream_keys(app.state.db_path, include_secret=True)
    assert stored["provider_id"] == "cline"
    assert stored["key_value"] == f"workos:{CLINE_AT}"
    assert json.loads(stored["metadata"])["refresh_token"] == CLINE_RT


async def test_provider_connections_expand_codex_and_cline_accounts(client):
    data = json.loads(_codex_connections())
    data["providerConnections"].append(
        {"provider": "cline", "accessToken": CLINE_AT, "refreshToken": CLINE_RT, "name": "cline"}
    )
    payload = (await _preview(client, json.dumps(data))).json()

    assert payload["new_count"] == 3
    assert {g["provider_id"]: g["count"] for g in payload["by_provider"]} == {
        "codex": 2,
        "cline": 1,
    }


async def test_bare_workos_token_detected_as_cline(client):
    item = (await _preview(client, f"workos:{CLINE_AT}")).json()["results"][0]

    assert (item["provider_id"], item["format"]) == ("cline", "api_key")


async def test_claude_code_credentials_are_accepted(client, app):
    blob = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-SECRET-claude-code-token",
                "refreshToken": "sk-ant-ort01-SECRET-claude-code-token",
                "expiresAt": 1759000000000,
                "scopes": ["user:inference"],
            }
        }
    )
    preview = await _preview(client, blob)
    submitted = await _submit(client, blob)

    item = preview.json()["results"][0]
    assert (item["status"], item["provider_id"], item["format"]) == (
        "new",
        "claude_oauth",
        "oauth_json",
    )
    assert submitted.status_code == 200
    assert submitted.json()["results"][0]["provider_id"] == "claude_oauth"
    for body in (preview.text, submitted.text):
        assert "SECRET" not in body
    stored = await list_upstream_keys(app.state.db_path)
    assert len(stored) == 1
    assert stored[0]["provider_id"] == "claude_oauth"
