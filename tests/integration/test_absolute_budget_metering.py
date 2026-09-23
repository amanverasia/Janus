import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.reload import reload_pricing, reload_providers
from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget, get_budget_status
from janus.storage.database import init_db, seed_from_config


@pytest.mark.parametrize("stream", [False, True])
async def test_completed_request_consumes_lifetime_budget_and_next_request_never_reaches_upstream(
    tmp_path, stream
):
    config = JanusConfig(
        server=ServerSettings(data_dir=tmp_path, require_api_key=True),
        providers=[
            ProviderConfig(
                id="test",
                prefix="test",
                api_type="openai_compat",
                base_url="https://fake.local/v1",
                api_key="sk-test-not-real",
                models=["metered-model"],
            )
        ],
        pricing={
            "metered-model": {
                "input_per_mtok": 1000,
                "output_per_mtok": 2000,
                "cache_creation_per_mtok": 0,
                "cache_read_per_mtok": 0,
            }
        },
    )
    app = create_app(config=config)
    db_path = app.state.db_path
    await init_db(db_path)
    await seed_from_config(db_path, config)
    await reload_providers(app)
    await reload_pricing(app)
    raw_key, record = await create_key(db_path, "metered-key")
    await create_or_update_budget(db_path, key_id=record["id"], absolute_limit=1)
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    if stream:
        chunks = [
            {
                "id": "completion-1",
                "object": "chat.completion.chunk",
                "model": "metered-model",
                "choices": [{"index": 0, "delta": {"content": "Hello!"}, "finish_reason": None}],
            },
            {
                "id": "completion-1",
                "object": "chat.completion.chunk",
                "model": "metered-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            },
        ]
        upstream_response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )
    else:
        upstream_response = httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "model": "metered-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            },
        )
    payload = {
        "model": "test/metered-model",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": stream,
    }
    try:
        with respx.mock as mock:
            upstream = mock.post("https://fake.local/v1/chat/completions").mock(
                return_value=upstream_response
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                accepted = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {raw_key}"},
                )
                assert accepted.status_code == 200
                assert "Hello!" in accepted.text
                status = await get_budget_status(db_path, key_id=record["id"])
                assert status["total_spend"] == pytest.approx(2)
                assert status["absolute_status"] == "exceeded"
                rejected = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {raw_key}"},
                )
                assert rejected.status_code == 429
                assert rejected.json()["error"]["budget_period"] == "absolute"
                assert "retry-after" not in rejected.headers
                assert upstream.call_count == 1
    finally:
        for provider in app.state.providers.values():
            await provider.close()
