import asyncio
import json
import time

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings

AUTH_KEY = "dashboard-auth-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_KEY}", "Accept": "application/json"}
PROBE_URL = "https://provider.example/v1/chat/completions"
pytestmark = pytest.mark.asyncio


def _app(tmp_path, providers: list[ProviderConfig] | None = None):
    return create_app(
        config=JanusConfig(
            server=ServerSettings(port=0, data_dir=tmp_path),
            providers=providers or [_openai_provider()],
            combos=[ComboConfig(name="test-combo", models=["test/model-1"])],
            api_keys=[AUTH_KEY],
        )
    )


def _openai_provider(**overrides) -> ProviderConfig:
    fields = {
        "id": "test-provider",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://provider.example/v1",
        "api_key": "provider-super-secret",
        "models": ["model-1"],
    }
    fields.update(overrides)
    return ProviderConfig(**fields)


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path=tmp_path)


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 4321)),
        base_url="http://test",
    )


def _chat_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "model-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


@respx.mock
async def test_probe_uses_provider_credential_and_first_model(app):
    route = respx.post(PROBE_URL).mock(return_value=_chat_ok())
    async with client_for(app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scope"] == "provider"
    assert body["model"] == "model-1"
    assert body["latency_ms"] >= 0
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer provider-super-secret"
    assert json.loads(sent.read())["model"] == "model-1"


@respx.mock
async def test_probe_reports_invalid_credentials_without_leaking_secret(app):
    respx.post(PROBE_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid API key"}})
    )
    async with client_for(app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == 401
    assert "Invalid API key" in body["error"]
    assert "provider-super-secret" not in r.text


@respx.mock
async def test_probe_timeout_returns_actionable_504(app, monkeypatch):
    import janus.dashboard.routes as dashboard_routes

    monkeypatch.setattr(dashboard_routes, "_PROBE_TIMEOUT_S", 0.2)

    async def slow(request):
        await asyncio.sleep(1.0)
        return _chat_ok()

    respx.post(PROBE_URL).mock(side_effect=slow)
    async with client_for(app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 504
    body = r.json()
    assert body["ok"] is False
    assert "did not respond" in body["error"]
    assert body["model"] == "model-1"


@respx.mock
async def test_probe_connect_failure_returns_actionable_502(app):
    respx.post(PROBE_URL).mock(side_effect=httpx.ConnectError("Failed to connect"))
    async with client_for(app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 502
    body = r.json()
    assert body["ok"] is False
    assert "provider.example" in body["error"]
    assert "DNS or network failure" in body["error"]


@respx.mock
async def test_probe_rejects_private_base_url(tmp_path):
    private_app = _app(
        providers=[_openai_provider(base_url="http://127.0.0.1:99/v1")], tmp_path=tmp_path
    )
    async with client_for(private_app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 400
    assert "internal/private" in r.json()["error"]


@respx.mock
async def test_probe_without_models_returns_422(tmp_path):
    bare_app = _app(providers=[_openai_provider(models=[])], tmp_path=tmp_path)
    async with client_for(bare_app) as client:
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 422
    assert "No model available" in r.json()["error"]


async def test_unsupported_api_type_lists_supported_types(app):
    from janus.storage.providers_db import create_provider

    async with client_for(app) as client:
        await client.get("/dashboard/api/v2/state/providers", headers=AUTH_HEADERS)
        await create_provider(
            app.state.db_path,
            {
                "id": "weird-provider",
                "prefix": "weird",
                "api_type": "carrier_pigeon",
                "base_url": "https://pigeon.example/v1",
                "models": ["model-p"],
            },
        )
        r = await client.post("/dashboard/api/providers/weird-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 400
    assert "carrier_pigeon" in r.json()["error"]
    assert "openai_compat" in r.json()["error"]


async def test_unknown_provider_returns_404(app):
    async with client_for(app) as client:
        r = await client.post("/dashboard/api/providers/nope/test", headers=AUTH_HEADERS)
    assert r.status_code == 404


async def _seed_inventory_key(app, provider_id="test", key_value="inv-key-1", label="acct-1"):
    from janus.storage.upstream_keys import create_upstream_key
    from janus.storage.upstream_models import replace_models_for_key

    row = await create_upstream_key(
        app.state.db_path,
        provider_id=provider_id,
        key_value=key_value,
        key_label=label,
    )
    await replace_models_for_key(
        app.state.db_path,
        upstream_key_id=row["id"],
        provider_id=provider_id,
        models=[{"model_id": "model-y", "is_available": 1}],
    )
    return row


@respx.mock
async def test_account_scope_probes_that_credentials_available_model(tmp_path):
    shared_app = _app(
        providers=[_openai_provider(api_key=None, models=["model-x"])], tmp_path=tmp_path
    )
    key_row = None
    async with client_for(shared_app) as client:
        await client.get("/dashboard/api/v2/state/providers", headers=AUTH_HEADERS)
        key_row = await _seed_inventory_key(shared_app)
        route = respx.post(PROBE_URL).mock(return_value=_chat_ok())
        r = await client.post(
            f"/dashboard/api/providers/test-provider/test?account={key_row['id']}",
            headers=AUTH_HEADERS,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scope"] == "account"
    assert body["account_label"] == "acct-1"
    assert body["model"] == "model-y"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer inv-key-1"
    assert json.loads(sent.read())["model"] == "model-y"


@respx.mock
async def test_aggregate_scope_avoids_first_model_false_negative(tmp_path):
    shared_app = _app(
        providers=[_openai_provider(api_key=None, models=["model-x"])], tmp_path=tmp_path
    )
    async with client_for(shared_app) as client:
        await client.get("/dashboard/api/v2/state/providers", headers=AUTH_HEADERS)
        await _seed_inventory_key(shared_app)
        route = respx.post(PROBE_URL).mock(return_value=_chat_ok())
        r = await client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "provider"
    assert body["model"] == "model-y"
    assert json.loads(route.calls.last.request.read())["model"] == "model-y"


async def test_unknown_account_returns_404(tmp_path):
    shared_app = _app(
        providers=[_openai_provider(api_key=None, models=["model-x"])], tmp_path=tmp_path
    )
    async with client_for(shared_app) as client:
        await client.get("/dashboard/api/v2/state/providers", headers=AUTH_HEADERS)
        r = await client.post(
            "/dashboard/api/providers/test-provider/test?account=does-not-exist",
            headers=AUTH_HEADERS,
        )
    assert r.status_code == 404


@respx.mock
async def test_codex_executor_probe_uses_responses_api(tmp_path):
    codex_app = _app(
        providers=[
            _openai_provider(
                id="codex-provider",
                prefix="codextest",
                api_type="codex",
                base_url="https://codex.example/v1",
                api_key="codex-key",
                models=["gpt-5-codex"],
            )
        ],
        tmp_path=tmp_path,
    )
    route = respx.post("https://codex.example/v1/responses").mock(
        return_value=httpx.Response(200, json={"id": "resp_1", "status": "completed"})
    )
    async with client_for(codex_app) as client:
        r = await client.post("/dashboard/api/providers/codex-provider/test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "gpt-5-codex"
    assert json.loads(route.calls.last.request.read())["model"] == "gpt-5-codex"


@respx.mock
async def test_copilot_oauth_executor_probe_exchanges_then_chats(tmp_path):
    copilot_app = _app(
        providers=[
            _openai_provider(
                id="copilot-provider",
                prefix="copilottest",
                api_type="github_copilot",
                base_url="https://api.githubcopilot.com",
                api_key="gho_oauth_token",
                models=["gpt-4o"],
            )
        ],
        tmp_path=tmp_path,
    )
    respx.get("https://api.github.com/copilot_internal/v2/token").mock(
        return_value=httpx.Response(
            200, json={"token": "copilot-session", "expires_at": time.time() + 1800}
        )
    )
    route = respx.post("https://api.githubcopilot.com/chat/completions").mock(
        return_value=_chat_ok()
    )
    async with client_for(copilot_app) as client:
        r = await client.post(
            "/dashboard/api/providers/copilot-provider/test", headers=AUTH_HEADERS
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert json.loads(route.calls.last.request.read())["model"] == "gpt-4o"


@respx.mock
async def test_dashboard_stays_responsive_during_slow_probe_dns(app, monkeypatch):
    import socket

    def slow_getaddrinfo(*args, **kwargs):
        time.sleep(1.0)
        return [(socket.AF_INET, None, None, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    respx.post(PROBE_URL).mock(return_value=_chat_ok())

    async with client_for(app) as client:
        warm = await client.get("/dashboard/api/v2/state/settings", headers=AUTH_HEADERS)
        assert warm.status_code == 200
        probe_task = asyncio.create_task(
            client.post("/dashboard/api/providers/test-provider/test", headers=AUTH_HEADERS)
        )
        await asyncio.sleep(0.1)
        start = time.perf_counter()
        settings = await client.get("/dashboard/api/v2/state/settings", headers=AUTH_HEADERS)
        responsive_elapsed = time.perf_counter() - start
        probe = await probe_task

    assert settings.status_code == 200
    assert responsive_elapsed < 0.6
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
