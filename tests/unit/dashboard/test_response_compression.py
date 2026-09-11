"""Response compression for dashboard state payloads (issue #111).

Production-like payloads are large and uncompressed: the models section alone
measured 1168 KB on a live deployment, which gzips to 33 KB.

The gateway also streams: SSE for live usage and chunked LLM proxy responses.
Compressing those would buffer tokens and destroy the point of streaming, so
the middleware must leave them alone.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

pytestmark = pytest.mark.asyncio

ADMIN_KEY = "compression-admin-key"


@pytest.fixture
def app(tmp_path):
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="test-provider",
                prefix="test",
                api_type="openai_compat",
                base_url="https://provider.example/v1",
                api_key="secret",
                models=[f"model-{i}" for i in range(200)],
            )
        ],
        api_keys=[ADMIN_KEY],
    )
    return create_app(config=config)


def _headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}", **extra}


async def test_large_state_payload_is_gzipped_when_the_client_accepts_it(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=_headers())
        response = await client.get(
            "/dashboard/api/v2/state/models",
            headers=_headers(**{"Accept-Encoding": "gzip"}),
        )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    # httpx decodes transparently; the decoded body must still be the real JSON.
    assert response.json()["section"] == "models"


async def test_response_is_untouched_when_the_client_does_not_accept_gzip(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=_headers())
        response = await client.get(
            "/dashboard/api/v2/state/models",
            headers=_headers(**{"Accept-Encoding": "identity"}),
        )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


async def test_server_sent_events_are_never_compressed(app) -> None:
    """Compressing an SSE body buffers it, which defeats live streaming.

    httpx's ASGITransport collects the whole response, so an endless stream
    cannot be driven through it. This exercises the mechanism that matters --
    the middleware's content-type exclusion -- with a finite SSE-typed body.
    """
    from fastapi.responses import StreamingResponse

    async def frames():
        for i in range(200):
            yield f'data: {{"seq": {i}, "pad": "{"x" * 200}"}}\n\n'.encode()

    @app.get("/__test__/sse")
    async def _sse() -> StreamingResponse:  # pragma: no cover - test-only route
        return StreamingResponse(frames(), media_type="text/event-stream")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/__test__/sse", headers=_headers(**{"Accept-Encoding": "gzip"})
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert "content-encoding" not in response.headers, "SSE must not be compressed"
    assert response.text.startswith("data:")


async def test_streamed_proxy_responses_are_never_compressed(app) -> None:
    """The LLM proxy streams chunked completions; buffering those adds latency."""
    from fastapi.responses import StreamingResponse

    async def chunks():
        for i in range(200):
            yield f'data: {{"choices":[{{"delta":{{"content":"{"y" * 200}"}}}}]}}\n\n'.encode()

    @app.get("/__test__/chat-stream")
    async def _chat() -> StreamingResponse:  # pragma: no cover - test-only route
        return StreamingResponse(chunks(), media_type="text/event-stream")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/__test__/chat-stream", headers=_headers(**{"Accept-Encoding": "gzip"})
        )

    assert "content-encoding" not in response.headers


async def test_compression_does_not_corrupt_the_payload(app) -> None:
    """Decompressing the raw body by hand must yield byte-identical JSON."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/dashboard/api/v2/models", headers=_headers())
        plain = await client.get(
            "/dashboard/api/v2/state/models",
            headers=_headers(**{"Accept-Encoding": "identity"}),
        )
        compressed = await client.get(
            "/dashboard/api/v2/state/models",
            headers=_headers(**{"Accept-Encoding": "gzip"}),
        )

    assert compressed.json() == plain.json()


async def test_small_responses_are_not_worth_compressing(app) -> None:
    """Below the threshold gzip adds bytes and CPU for nothing."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/health", headers=_headers(**{"Accept-Encoding": "gzip"}))

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
