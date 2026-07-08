import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings


async def _seed_and_reload(app) -> None:
    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.storage.database import init_db, seed_from_config

    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, app.state.config)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)


@pytest.fixture
async def two_model_two_account_app(tmp_path):
    """Two accounts under one prefix; each serves models m1 and m2."""
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="acct-a",
                prefix="test",
                api_type="openai_compat",
                base_url="https://a.local/v1",
                api_key="sk-a",
                models=["m1", "m2"],
            ),
            ProviderConfig(
                id="acct-b",
                prefix="test",
                api_type="openai_compat",
                base_url="https://b.local/v1",
                api_key="sk-b",
                models=["m1", "m2"],
            ),
        ],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.fixture
async def combo_two_account_app(tmp_path):
    """A combo mapping to test/m1, served by two accounts."""
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="acct-a",
                prefix="test",
                api_type="openai_compat",
                base_url="https://a.local/v1",
                api_key="sk-a",
                models=["m1"],
            ),
            ProviderConfig(
                id="acct-b",
                prefix="test",
                api_type="openai_compat",
                base_url="https://b.local/v1",
                api_key="sk-b",
                models=["m1"],
            ),
        ],
        combos=[ComboConfig(name="combo1", models=["test/m1"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


def _completion(model: str) -> dict:
    return {
        "id": "r1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
@respx.mock
async def test_per_model_cooldown_isolates_failed_model(two_model_two_account_app):
    app = two_model_two_account_app
    # Account A rejects m1 with 429 but would serve fine otherwise; B serves m1.
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("m1"))
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200

    handler = app.state.fallback_handler
    targets = app.state.registry.lookup("test/m1")
    assert targets is not None
    account_a = next(t.account_id for t in targets if t.provider_config.id == "acct-a")

    # m1 on account A is cooled down; m2 on account A is unaffected.
    assert not handler.is_available(account_a, "m1")
    assert handler.is_available(account_a, "m2")


@pytest.mark.asyncio
@respx.mock
async def test_combo_cooldown_lands_on_real_model_not_combo_name(combo_two_account_app):
    app = combo_two_account_app
    # Account A always rejects with 429; account B always succeeds.
    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("m1"))
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "combo1", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200

    handler = app.state.fallback_handler
    targets = app.state.registry.lookup("test/m1")
    assert targets is not None
    account_a = next(t.account_id for t in targets if t.provider_config.id == "acct-a")

    # The cooldown must land on the real model key ("m1"), not the combo name.
    assert not handler.is_available(account_a, "m1")
