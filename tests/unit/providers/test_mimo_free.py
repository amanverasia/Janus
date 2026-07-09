import base64
import json

import httpx
import pytest
import respx

from janus.providers.mimo_free import (
    MIMO_SYSTEM_MARKER,
    MimoFreeProvider,
    inject_system_marker,
    parse_jwt_exp_ms,
)


def test_inject_system_marker_prepends_once() -> None:
    body = {"messages": [{"role": "user", "content": "hi"}]}
    out = inject_system_marker(body)
    assert out["messages"][0]["content"] == MIMO_SYSTEM_MARKER
    out2 = inject_system_marker(out)
    assert sum(1 for m in out2["messages"] if m.get("role") == "system") == 1


def test_parse_jwt_exp_ms() -> None:
    raw = json.dumps({"exp": 4_900_000_000}).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    jwt = f"h.{payload}.s"
    assert parse_jwt_exp_ms(jwt) == 4_900_000_000_000


@pytest.mark.asyncio
@respx.mock
async def test_mimo_free_bootstrap_and_headers() -> None:
    respx.post("https://api.xiaomimimo.com/api/free-ai/bootstrap").mock(
        return_value=httpx.Response(200, json={"jwt": "hdr.eyJleHAiOjQ5MDAwMDAwMDB9.sig"})
    )
    route = respx.post("https://api.xiaomimimo.com/api/free-ai/openai/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    p = MimoFreeProvider()
    result = await p.call({"model": "mimo-auto", "messages": [{"role": "user", "content": "hi"}]})
    assert result.status_code == 200
    req = route.calls.last.request
    assert req.headers["authorization"].startswith("Bearer ")
    assert req.headers["x-mimo-source"] == "mimocode-cli-free"
    assert req.headers["x-session-affinity"].startswith("ses_")
    body = json.loads(req.content)
    assert body["messages"][0]["content"] == MIMO_SYSTEM_MARKER
    await p.close()


@pytest.mark.asyncio
@respx.mock
async def test_mimo_free_rebootstrap_on_401() -> None:
    boot = respx.post("https://api.xiaomimimo.com/api/free-ai/bootstrap").mock(
        side_effect=[
            httpx.Response(200, json={"jwt": "old.jwt.token"}),
            httpx.Response(200, json={"jwt": "new.jwt.token"}),
        ]
    )
    chat = respx.post("https://api.xiaomimimo.com/api/free-ai/openai/chat").mock(
        side_effect=[
            httpx.Response(401, json={"error": "expired"}),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            ),
        ]
    )
    p = MimoFreeProvider()
    result = await p.call({"model": "mimo-auto", "messages": [{"role": "user", "content": "hi"}]})
    assert result.status_code == 200
    assert boot.call_count == 2
    assert chat.call_count == 2
    assert chat.calls.last.request.headers["authorization"] == "Bearer new.jwt.token"
    await p.close()
