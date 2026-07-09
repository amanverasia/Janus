import time

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings


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
async def two_account_app(tmp_path):
    """Two accounts under the same prefix, distinct base URLs so respx can tell them apart."""
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
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    return app


@pytest.mark.asyncio
@respx.mock
async def test_repeated_429_escalates_backoff_for_specific_model(two_account_app):
    targets = two_account_app.state.registry.lookup("test/m1")
    account_a_id = next(t.account_id for t in targets if t.provider_config.id == "acct-a")

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    route_b = respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        payload = {
            "model": "test/m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }
        response1 = await client.post("/v1/chat/completions", json=payload)
        assert response1.status_code == 200

        handler = two_account_app.state.fallback_handler
        handler._cooldowns[(account_a_id, "m1")] = time.time() - 1
        handler._rotation_counters["test/m1"] = 0

        response2 = await client.post("/v1/chat/completions", json=payload)
        assert response2.status_code == 200

    assert route_b.call_count == 2

    handler = two_account_app.state.fallback_handler
    assert handler._backoff[(account_a_id, "m1")] >= 2


class _MidStreamFailure(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"id":"r1","object":"chat.completion.chunk",'
            b'"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        )
        raise httpx.ReadError("connection lost mid-stream")


@pytest.mark.asyncio
@respx.mock
async def test_mid_stream_failure_does_not_mark_success(two_account_app):
    targets = two_account_app.state.registry.lookup("test/m1")
    account_a_id = next(t.account_id for t in targets if t.provider_config.id == "acct-a")

    route_a = respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, stream=_MidStreamFailure())
    )

    handler = two_account_app.state.fallback_handler
    original_mark_success = handler.mark_success
    mark_success_calls: list[tuple[str, str | None]] = []

    def _spy_mark_success(account_id: str, model: str | None = None) -> None:
        mark_success_calls.append((account_id, model))
        original_mark_success(account_id, model)

    handler.mark_success = _spy_mark_success

    async with AsyncClient(
        transport=ASGITransport(app=two_account_app), base_url="http://test"
    ) as client:
        payload = {
            "model": "test/m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with pytest.raises(httpx.ReadError):
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                assert response.status_code == 200
                async for _ in response.aiter_bytes():
                    pass

    assert route_a.call_count == 1
    assert mark_success_calls == []
    assert handler._cooldowns.get((account_a_id, "m1")) is None
    assert handler._backoff.get((account_a_id, "m1")) is None
