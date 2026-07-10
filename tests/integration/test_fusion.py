"""Integration tests for the fusion combo strategy (panel fan-out + judge)."""

import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import ComboConfig, JanusConfig, ProviderConfig, ServerSettings
from janus.storage.settings import set_setting


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


def _completion(content: str, model: str) -> dict:
    return {
        "id": "r",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


async def _fusion_app(tmp_path):
    provider1 = ProviderConfig(
        id="a",
        prefix="a",
        api_type="openai_compat",
        base_url="https://a.local/v1",
        api_key="k1",
        models=["m1"],
    )
    provider2 = ProviderConfig(
        id="b",
        prefix="b",
        api_type="openai_compat",
        base_url="https://b.local/v1",
        api_key="k2",
        models=["m2"],
    )
    cfg = JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=[provider1, provider2],
        combos=[ComboConfig(name="fus", models=["a/m1", "b/m2"])],
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)
    await set_setting(app.state.db_path, "combo_strategy", "fusion")
    return app


@pytest.mark.asyncio
@respx.mock
async def test_fusion_panel_and_judge(tmp_path):
    app = await _fusion_app(tmp_path)

    a_bodies: list[dict] = []
    b_bodies: list[dict] = []

    def a_responder(request):
        body = json.loads(request.content)
        a_bodies.append(body)
        # First call is the panel request, second is the judge request.
        text = "final synthesis" if len(a_bodies) > 1 else "panel one"
        return httpx.Response(200, json=_completion(text, "m1"))

    def b_responder(request):
        b_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion("panel two", "m2"))

    respx.post("https://a.local/v1/chat/completions").mock(side_effect=a_responder)
    respx.post("https://b.local/v1/chat/completions").mock(side_effect=b_responder)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "fus", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "final synthesis"

    # Both panel members were called; judge (panel[0]) was called a second time.
    assert len(b_bodies) == 1
    assert len(a_bodies) == 2

    # Panel requests are forced non-streaming.
    assert not a_bodies[0].get("stream")
    assert not b_bodies[0].get("stream")

    # Judge request = original conversation + appended synthesis user turn.
    judge_messages = a_bodies[1]["messages"]
    assert judge_messages[0]["content"] == "hi"
    judge_turn = judge_messages[-1]
    assert judge_turn["role"] == "user"
    assert "[Source 1]" in judge_turn["content"]
    assert "[Source 2]" in judge_turn["content"]
    assert "panel one" in judge_turn["content"]
    assert "panel two" in judge_turn["content"]


@pytest.mark.asyncio
@respx.mock
async def test_fusion_records_panel_usage(tmp_path):
    app = await _fusion_app(tmp_path)

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("one", "m1"))
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("two", "m2"))
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "fus", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 200

    from janus.storage.database import get_connection

    async with get_connection(app.state.db_path) as db:
        async with db.execute(
            "SELECT model, input_tokens, output_tokens FROM usage WHERE status = 200"
        ) as cur:
            rows = await cur.fetchall()
    by_model = [(row["model"], row["input_tokens"], row["output_tokens"]) for row in rows]
    # Two panel calls + one judge call, all with real token counts.
    assert by_model.count(("m1", 3, 5)) == 2
    assert ("m2", 3, 5) in by_model


@pytest.mark.asyncio
@respx.mock
async def test_fusion_judge_combo_rejected(tmp_path):
    app = await _fusion_app(tmp_path)
    await set_setting(app.state.db_path, "combo_fusion_judge", "fus")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "fus", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_fusion_all_panel_failed_returns_503(tmp_path):
    app = await _fusion_app(tmp_path)

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://b.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "fus", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 503
