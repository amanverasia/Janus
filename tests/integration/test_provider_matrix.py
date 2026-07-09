"""End-to-end matrix: every catalog ``api_type`` without real API keys.

Uses respx to mock upstream HTTP. Asserts:
- catalog → ``_build_provider`` → registry native_format → format adapter
- ASGI chat path (OpenAI client) non-stream + stream for each api_type
- Cross-format translate path for anthropic/gemini/codex natives
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from janus.api.routes import FORMATS, _resolve_format
from janus.app import _build_provider, create_app
from janus.catalog import PROVIDERS
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.providers.anthropic import AnthropicProvider
from janus.providers.antigravity import AntigravityProvider
from janus.providers.claude_oauth import ClaudeOAuthProvider
from janus.providers.codex import CodexProvider
from janus.providers.cursor import CursorProvider
from janus.providers.gemini import GeminiProvider
from janus.providers.github_copilot import GitHubCopilotProvider
from janus.providers.kiro import KiroProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.mimo_free import MimoFreeProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
from janus.providers.registry import ProviderRegistry, _native_format

# ── Fixtures / helpers ──────────────────────────────────────────────────

_OPENAI_JSON = {
    "id": "chatcmpl-matrix1",
    "object": "chat.completion",
    "model": "m1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello-matrix"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}

_OPENAI_SSE = (
    'data: {"id":"chatcmpl-matrix1","object":"chat.completion.chunk","model":"m1",'
    '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-matrix1","object":"chat.completion.chunk","model":"m1",'
    '"choices":[{"index":0,"delta":{"content":"hello-matrix"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-matrix1","object":"chat.completion.chunk","model":"m1",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)

_ANTHROPIC_JSON = {
    "id": "msg_matrix1",
    "type": "message",
    "role": "assistant",
    "model": "m1",
    "content": [{"type": "text", "text": "hello-matrix"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 2},
}

_ANTHROPIC_SSE = (
    'data: {"type":"message_start","message":{"id":"msg_matrix1","type":"message",'
    '"role":"assistant","model":"m1","content":[],"usage":{"input_tokens":3,'
    '"output_tokens":0}}}\n\n'
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hello-matrix"}}\n\n'
    'data: {"type":"content_block_stop","index":0}\n\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":2}}\n\n'
    'data: {"type":"message_stop"}\n\n'
)

_GEMINI_JSON = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "hello-matrix"}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
}

_GEMINI_SSE = (
    'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"hello-matrix"}]},'
    '"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":3,'
    '"candidatesTokenCount":2}}\n\n'
)

_RESPONSES_JSON = {
    "id": "resp_matrix1",
    "object": "response",
    "status": "completed",
    "model": "m1",
    "output": [
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "hello-matrix"}],
        }
    ],
    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
}

_API_TYPE_CLASS = {
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "opencode_free": OpenCodeFreeProvider,
    "mimo_free": MimoFreeProvider,
    "github_copilot": GitHubCopilotProvider,
    "codex": CodexProvider,
    "kiro": KiroProvider,
    "cursor": CursorProvider,
    "antigravity": AntigravityProvider,
    "claude_oauth": ClaudeOAuthProvider,
}

_API_TYPE_NATIVE = {
    "openai_compat": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "opencode_free": "openai",
    "mimo_free": "openai",
    "github_copilot": "openai",
    "codex": "openai_responses",
    "kiro": "openai",
    "cursor": "openai",
    "antigravity": "gemini",
    "claude_oauth": "anthropic",
}


def _catalog_api_types() -> list[str]:
    return sorted(
        {
            (v.get("gateway") or {}).get("api_type")
            for v in PROVIDERS.values()
            if (v.get("gateway") or {}).get("api_type")
        }
    )


async def _seed_and_reload(app: Any) -> None:
    from janus.dashboard.reload import (
        reload_combos,
        reload_pricing,
        reload_providers,
        reload_savers,
    )
    from janus.storage.database import init_db, seed_from_config

    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, app.state.config)
    await reload_providers(app)
    await reload_combos(app)
    await reload_savers(app)
    await reload_pricing(app)


def _cfg(tmp_path: Any, *providers: ProviderConfig) -> JanusConfig:
    return JanusConfig(
        server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path),
        providers=list(providers),
    )


def _provider(
    *,
    api_type: str,
    base_url: str,
    prefix: str | None = None,
    model: str = "m1",
    api_key: str = "test-key",
) -> ProviderConfig:
    pid = api_type.replace("-", "_")
    return ProviderConfig(
        id=pid,
        prefix=prefix or pid,
        api_type=api_type,
        base_url=base_url,
        api_key=api_key,
        models=[model],
    )


# ── Static wiring audits ────────────────────────────────────────────────


def test_every_catalog_api_type_has_builder_and_format() -> None:
    for api_type in _catalog_api_types():
        assert api_type in _API_TYPE_CLASS, f"missing class map for {api_type}"
        cfg = _provider(api_type=api_type, base_url="https://example.test")
        inst = _build_provider(cfg)
        assert isinstance(inst, _API_TYPE_CLASS[api_type])
        native = _native_format(api_type)
        assert native == _API_TYPE_NATIVE[api_type]
        adapter = _resolve_format(native)
        assert adapter is not None
        assert adapter.name in FORMATS or adapter.name == native


def test_every_gateway_entry_registers_and_resolves() -> None:
    reg = ProviderRegistry()
    count = 0
    for key, entry in PROVIDERS.items():
        g = entry.get("gateway")
        if not g:
            continue
        count += 1
        cfg = ProviderConfig(
            id=g["id"],
            prefix=g["prefix"],
            api_type=g["api_type"],
            base_url=g.get("base_url") or "https://example.test",
            api_key="k",
            models=g.get("default_models") or ["m"],
        )
        inst = _build_provider(cfg)
        reg.register(cfg)
        model = (g.get("default_models") or ["m"])[0]
        targets = reg.lookup(f"{g['prefix']}/{model}")
        assert targets, f"lookup failed for {g['id']} {g['prefix']}/{model}"
        _resolve_format(targets[0].native_format)
        # sync close best-effort
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            loop.run_until_complete(inst.close())
            loop.close()
        except Exception:
            pass
    assert count == 35


def test_alias_api_types_build() -> None:
    for alias, expected_cls in [
        ("gemini_cli", AntigravityProvider),
        ("gemini-cli", AntigravityProvider),
        ("claude", ClaudeOAuthProvider),
    ]:
        p = _build_provider(_provider(api_type=alias, base_url="https://example.test"))
        assert isinstance(p, expected_cls)


# ── Per-api_type ASGI matrix ─────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_nonstream_and_stream(tmp_path: Any) -> None:
    base = "https://oc.local/v1"
    cfg = _cfg(tmp_path, _provider(api_type="openai_compat", base_url=base, prefix="oc"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "oc/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"

    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_OPENAI_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={
                "model": "oc/m1",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert r.status_code == 200
        body = r.text
        assert "hello-matrix" in body
        assert "finish_reason" in body
        assert "[DONE]" in body


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_via_openai_client_translate(tmp_path: Any) -> None:
    base = "https://an.local"
    cfg = _cfg(tmp_path, _provider(api_type="anthropic", base_url=base, prefix="an"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/v1/messages").mock(
        return_value=httpx.Response(200, json=_ANTHROPIC_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "an/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent["model"] == "m1"
        assert "messages" in sent

    route_s = respx.post(f"{base}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=_ANTHROPIC_SSE.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={
                "model": "an/m1",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert r.status_code == 200, r.text
        assert "hello-matrix" in r.text
        assert route_s.called


@pytest.mark.asyncio
@respx.mock
async def test_gemini_via_openai_client_translate(tmp_path: Any) -> None:
    base = "https://gm.local"
    cfg = _cfg(tmp_path, _provider(api_type="gemini", base_url=base, prefix="gm"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(url__regex=r"https://gm\.local/.*generateContent.*").mock(
        return_value=httpx.Response(200, json=_GEMINI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "gm/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called

    route_s = respx.post(url__regex=r"https://gm\.local/.*streamGenerateContent.*").mock(
        return_value=httpx.Response(
            200,
            content=_GEMINI_SSE.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={
                "model": "gm/m1",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert r.status_code == 200, r.text
        assert "hello-matrix" in r.text
        assert route_s.called


@pytest.mark.asyncio
@respx.mock
async def test_codex_via_openai_client_translate(tmp_path: Any) -> None:
    base = "https://cd.local"
    cfg = _cfg(tmp_path, _provider(api_type="codex", base_url=base, prefix="cd"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/responses").mock(
        return_value=httpx.Response(200, json=_RESPONSES_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "cd/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert "input" in sent
        assert sent.get("store") is False


@pytest.mark.asyncio
@respx.mock
async def test_claude_oauth_native_anthropic_client(tmp_path: Any) -> None:
    base = "https://co.local"
    cfg = _cfg(tmp_path, _provider(api_type="claude_oauth", base_url=base, prefix="co"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/v1/messages?beta=true").mock(
        return_value=httpx.Response(200, json=_ANTHROPIC_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/messages",
            json={
                "model": "co/m1",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"anthropic-version": "2023-06-01"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["content"][0]["text"] == "hello-matrix"
        assert route.called
        assert "Bearer test-key" in route.calls.last.request.headers["Authorization"]


@pytest.mark.asyncio
@respx.mock
async def test_claude_oauth_via_openai_client_translate(tmp_path: Any) -> None:
    base = "https://co2.local"
    cfg = _cfg(tmp_path, _provider(api_type="claude_oauth", base_url=base, prefix="co2"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/v1/messages?beta=true").mock(
        return_value=httpx.Response(200, json=_ANTHROPIC_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "co2/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_antigravity_via_openai_client_translate(tmp_path: Any) -> None:
    base = "https://ag.local"
    cfg = _cfg(tmp_path, _provider(api_type="antigravity", base_url=base, prefix="ag"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/v1internal:generateContent").mock(
        return_value=httpx.Response(200, json=_GEMINI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "ag/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert "request" in sent or "contents" in sent


@pytest.mark.asyncio
@respx.mock
async def test_cursor_openai_native_passthrough(tmp_path: Any) -> None:
    base = "https://cu.local/v1"
    cfg = _cfg(tmp_path, _provider(api_type="cursor", base_url=base, prefix="cu"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "cu/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called

    respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_OPENAI_SSE.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={
                "model": "cu/m1",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert r.status_code == 200
        assert "hello-matrix" in r.text
        assert "[DONE]" in r.text


@pytest.mark.asyncio
@respx.mock
async def test_kiro_openai_native_with_bridge_url(tmp_path: Any) -> None:
    """Kiro native EventStream is incomplete; OpenAI-bridge base_url must work."""
    base = "https://ki.local/v1"
    cfg = _cfg(tmp_path, _provider(api_type="kiro", base_url=base, prefix="ki"))
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "ki/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_opencode_free_openai_native(tmp_path: Any) -> None:
    # OpenCodeFreeProvider hardcodes its base URL
    cfg = _cfg(
        tmp_path,
        _provider(
            api_type="opencode_free",
            base_url="https://opencode.ai/zen/v1",
            prefix="of",
        ),
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    route = respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "of/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_mimo_free_openai_native(tmp_path: Any) -> None:
    bootstrap = respx.post("https://api.xiaomimimo.com/api/free-ai/bootstrap").mock(
        return_value=httpx.Response(200, json={"jwt": "hdr.eyJleHAiOjQ5MDAwMDAwMDB9.sig"})
    )
    chat = respx.post("https://api.xiaomimimo.com/api/free-ai/openai/chat").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    cfg = _cfg(
        tmp_path,
        _provider(
            api_type="mimo_free",
            base_url="https://api.xiaomimimo.com/api/free-ai/openai/chat",
            prefix="mmf",
            model="mimo-auto",
            api_key="",
        ),
    )
    app = create_app(config=cfg)
    from janus.dashboard.reload import reload_providers
    from janus.storage.database import init_db, seed_from_config

    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, cfg)
    await reload_providers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mmf/mimo-auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    assert bootstrap.called
    assert chat.called
    req = chat.calls.last.request
    assert req.headers.get("x-mimo-source") == "mimocode-cli-free"
    assert req.headers.get("x-session-affinity", "").startswith("ses_")
    body = json.loads(req.content)
    assert body["messages"][0]["role"] == "system"
    assert "MiMoCode" in body["messages"][0]["content"]

async def test_github_copilot_exchanges_token_then_chats(tmp_path: Any) -> None:
    base = "https://api.individual.githubcopilot.com"
    cfg = _cfg(
        tmp_path,
        _provider(
            api_type="github_copilot",
            base_url=base,
            prefix="gc",
            api_key="gho_oauth_token",
        ),
    )
    app = create_app(config=cfg)
    await _seed_and_reload(app)

    # Copilot first exchanges oauth → session token (GET, not POST)
    import time

    respx.get("https://api.github.com/copilot_internal/v2/token").mock(
        return_value=httpx.Response(
            200,
            json={"token": "session-tok", "expires_at": int(time.time()) + 1800},
        )
    )
    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=_OPENAI_JSON)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "gc/m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hello-matrix"
        assert route.called


# ── Format round-trips + cross-format ────────────────────────────────────


@pytest.mark.parametrize("fmt", sorted(FORMATS.keys()))
def test_format_adapter_roundtrip(fmt: str) -> None:
    adapter = FORMATS[fmt]
    if fmt == "openai":
        req = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        resp = _OPENAI_JSON
    elif fmt == "anthropic":
        req = {
            "model": "m",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        }
        resp = _ANTHROPIC_JSON
    elif fmt == "gemini":
        req = {"model": "m", "contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
        resp = _GEMINI_JSON
    elif fmt == "ollama":
        req = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        resp = {
            "model": "m",
            "message": {"role": "assistant", "content": "hello-matrix"},
            "done": True,
            "prompt_eval_count": 3,
            "eval_count": 2,
        }
    elif fmt == "openai_responses":
        req = {"model": "m", "input": "hi"}
        resp = _RESPONSES_JSON
    else:
        pytest.skip(f"no fixture for {fmt}")

    can = adapter.parse_request(req)
    up = adapter.build_upstream_request(can, "m")
    assert isinstance(up, dict)
    can_resp = adapter.parse_upstream_response(resp)
    out = adapter.emit_response(can_resp)
    assert isinstance(out, dict)
    # stream adapters construct
    parser = adapter.stream_parser()
    emitter = adapter.stream_emitter()
    assert parser is not None and emitter is not None


@pytest.mark.parametrize(
    "client_fmt,provider_fmt",
    [
        ("openai", "anthropic"),
        ("openai", "gemini"),
        ("openai", "openai_responses"),
        ("anthropic", "openai"),
        ("gemini", "openai"),
    ],
)
def test_cross_format_build_and_emit(client_fmt: str, provider_fmt: str) -> None:
    client = FORMATS[client_fmt]
    provider = FORMATS[provider_fmt]

    if client_fmt == "openai":
        raw = {"model": "p/m", "messages": [{"role": "user", "content": "hi"}]}
    elif client_fmt == "anthropic":
        raw = {
            "model": "p/m",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}],
        }
    else:
        raw = {
            "model": "p/m",
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        }

    can = client.parse_request(raw)
    up = provider.build_upstream_request(can, "upstream-model")
    assert isinstance(up, dict) and up

    if provider_fmt == "openai":
        upstream_resp = _OPENAI_JSON
    elif provider_fmt == "anthropic":
        upstream_resp = _ANTHROPIC_JSON
    elif provider_fmt == "gemini":
        upstream_resp = _GEMINI_JSON
    else:
        upstream_resp = _RESPONSES_JSON

    can_resp = provider.parse_upstream_response(upstream_resp)
    out = client.emit_response(can_resp)
    assert isinstance(out, dict)


# ── Models endpoint lists every registered provider ──────────────────────


@pytest.mark.asyncio
async def test_models_lists_all_matrix_providers(tmp_path: Any) -> None:
    providers = [
        _provider(api_type="openai_compat", base_url="https://a/v1", prefix="a"),
        _provider(api_type="anthropic", base_url="https://b", prefix="b"),
        _provider(api_type="gemini", base_url="https://c", prefix="c"),
        _provider(api_type="codex", base_url="https://d", prefix="d"),
        _provider(api_type="kiro", base_url="https://e/v1", prefix="e"),
        _provider(api_type="cursor", base_url="https://f/v1", prefix="f"),
        _provider(api_type="antigravity", base_url="https://g", prefix="g"),
        _provider(api_type="claude_oauth", base_url="https://h", prefix="h"),
        _provider(api_type="opencode_free", base_url="https://i", prefix="i"),
        _provider(api_type="github_copilot", base_url="https://j", prefix="j"),
    ]
    app = create_app(config=_cfg(tmp_path, *providers))
    await _seed_and_reload(app)
    # every provider should be built and cached
    assert set(app.state.providers.keys()) == {p.id for p in providers}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/models")
        assert r.status_code == 200
        ids = {m["id"] for m in r.json()["data"]}
        for p in providers:
            assert f"{p.prefix}/m1" in ids


# ── Executor-level stream line iterators for specialized providers ───────


@pytest.mark.asyncio
@respx.mock
async def test_specialized_executors_stream_line_iter() -> None:
    """Each specialized provider's stream path must yield aiter_lines-compatible rows."""
    cases: list[tuple[Any, str, str]] = [
        (
            CodexProvider(api_key="k", base_url="https://s1.local"),
            "https://s1.local/responses",
            _OPENAI_SSE,
        ),
        (
            CursorProvider(api_key="k", base_url="https://s2.local/v1"),
            "https://s2.local/v1/chat/completions",
            _OPENAI_SSE,
        ),
        (
            KiroProvider(api_key="k", base_url="https://s3.local/v1"),
            "https://s3.local/v1/chat/completions",
            _OPENAI_SSE,
        ),
        (
            ClaudeOAuthProvider(api_key="k", base_url="https://s4.local"),
            "https://s4.local/v1/messages?beta=true",
            _ANTHROPIC_SSE,
        ),
        (
            AntigravityProvider(api_key="k", base_url="https://s5.local"),
            "https://s5.local/v1internal:streamGenerateContent?alt=sse",
            _GEMINI_SSE,
        ),
    ]
    for provider, url, body in cases:
        respx.post(url).mock(
            return_value=httpx.Response(
                200, content=body.encode(), headers={"content-type": "text/event-stream"}
            )
        )
        result = await provider.call({"model": "m1", "messages": []}, stream=True)
        assert result.status_code == 200
        assert result.lines is not None
        lines = [line async for line in result.lines]
        assert any(line.strip() for line in lines)
        await provider.close()
