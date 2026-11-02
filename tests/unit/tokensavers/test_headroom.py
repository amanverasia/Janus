import asyncio
import time

import httpx
import respx

from janus.canonical.models import (
    CanonicalRequest,
    Message,
    Role,
    SystemBlock,
    TextPart,
)
from janus.tokensavers.headroom import HeadroomSaver
from janus.tokensavers.pipeline import SaverPipeline


def _req() -> CanonicalRequest:
    return CanonicalRequest(
        model="test/m1",
        system=[SystemBlock(text="be brief")],
        messages=[Message(role=Role.USER, content=[TextPart(text="long " * 100)])],
        max_tokens=256,
    )


@respx.mock
async def test_transform_swaps_compressed_messages():
    respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(
            200,
            json={
                "messages": [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "long (compressed)"},
                ]
            },
        )
    )
    saver = HeadroomSaver()
    result = await saver.transform(_req())
    await saver.close()
    assert result.system[0].text == "be brief"
    assert result.messages[0].content[0].text == "long (compressed)"
    assert result.max_tokens == 256
    assert result.model == "test/m1"


@respx.mock
async def test_transform_sends_openai_messages():
    route = respx.post("http://headroom.local/v1/compress").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    saver = HeadroomSaver(base_url="http://headroom.local/")
    original = _req()
    result = await saver.transform(original)
    await saver.close()
    assert result is original

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test/m1"
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    assert sent["messages"][1]["role"] == "user"


@respx.mock
async def test_fails_open_on_http_error():
    respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(500, text="boom")
    )
    saver = HeadroomSaver()
    original = _req()
    assert await saver.transform(original) is original
    await saver.close()


@respx.mock
async def test_fails_open_on_network_error():
    respx.post("http://localhost:8787/v1/compress").mock(side_effect=httpx.ConnectError("refused"))
    saver = HeadroomSaver()
    original = _req()
    assert await saver.transform(original) is original
    await saver.close()


@respx.mock
async def test_fails_open_on_malformed_response():
    respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    saver = HeadroomSaver()
    original = _req()
    assert await saver.transform(original) is original
    await saver.close()


async def test_pipeline_apply_async_catches_exceptions():
    class BadAsyncSaver:
        async def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            raise RuntimeError("boom")

        async def close(self) -> None:
            pass

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([], [BadAsyncSaver()])
    result = await pipeline.apply_async(req)
    assert result is req
    await pipeline.close()


async def test_pipeline_apply_async_noop_without_async_savers():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([])
    result = await pipeline.apply_async(req)
    assert result is req


@respx.mock
async def test_probe_reports_reachable_and_caches():
    route = respx.get("http://localhost:8787/health").mock(return_value=httpx.Response(200))
    saver = HeadroomSaver()
    first = await saver.check_health()
    second = await saver.check_health()
    await saver.close()
    assert first["reachable"] is True
    assert first["reason"] is None
    assert first["checked_at"]
    assert second == first
    assert route.call_count == 1


@respx.mock
async def test_probe_reports_unreachable_with_reason():
    respx.get("http://localhost:8787/health").mock(side_effect=httpx.ConnectError("refused"))
    saver = HeadroomSaver()
    status = await saver.check_health()
    await saver.close()
    assert status["reachable"] is False
    assert status["reason"] == "Connection refused or host unreachable"


@respx.mock
async def test_probe_reports_timeout_and_server_error():
    respx.get("http://slow.local/health").mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get("http://sick.local/health").mock(return_value=httpx.Response(503))
    slow = HeadroomSaver(base_url="http://slow.local")
    sick = HeadroomSaver(base_url="http://sick.local")
    assert (await slow.check_health())["reason"] == "Timed out waiting for Headroom"
    assert (await sick.check_health())["reason"] == "Headroom returned HTTP 503"
    await slow.close()
    await sick.close()


@respx.mock
async def test_unknown_health_does_not_block_first_request():
    compress = respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(
            200, json={"messages": [{"role": "user", "content": "compressed"}]}
        )
    )
    saver = HeadroomSaver()
    assert saver.health()["reachable"] is None
    result = await saver.transform(_req())
    await saver.close()
    assert compress.call_count == 1
    assert result.messages[0].content[0].text == "compressed"
    assert saver.health()["reachable"] is True


@respx.mock
async def test_connection_failure_opens_circuit_and_skips_network():
    compress = respx.post("http://localhost:8787/v1/compress").mock(
        side_effect=httpx.ConnectError("refused")
    )
    respx.get("http://localhost:8787/health").mock(side_effect=httpx.ConnectError("refused"))
    saver = HeadroomSaver()
    original = _req()
    assert await saver.transform(original) is original
    assert saver.health()["reachable"] is False
    for _ in range(3):
        assert await saver.transform(original) is original
    await saver.close()
    assert compress.call_count == 1


@respx.mock
async def test_http_5xx_from_compress_opens_circuit():
    compress = respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(500, text="boom")
    )
    saver = HeadroomSaver()
    original = _req()
    await saver.transform(original)
    await saver.transform(original)
    await saver.close()
    assert compress.call_count == 1
    assert saver.health()["reason"] == "Headroom returned HTTP 500"


@respx.mock
async def test_http_4xx_from_compress_keeps_circuit_closed():
    compress = respx.post("http://localhost:8787/v1/compress").mock(
        return_value=httpx.Response(422, text="bad")
    )
    saver = HeadroomSaver()
    original = _req()
    await saver.transform(original)
    await saver.transform(original)
    await saver.close()
    assert compress.call_count == 2
    assert saver.health()["reachable"] is True


@respx.mock
async def test_circuit_recovers_automatically_after_ttl():
    compress = respx.post("http://localhost:8787/v1/compress").mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"messages": [{"role": "user", "content": "compressed"}]}),
        ]
    )
    health = respx.get("http://localhost:8787/health").mock(return_value=httpx.Response(200))
    saver = HeadroomSaver(health_ttl=0.05)
    original = _req()
    await saver.transform(original)
    assert saver.health()["reachable"] is False
    await asyncio.sleep(0.08)
    skipped = await saver.transform(original)
    assert skipped is original
    assert compress.call_count == 1
    assert saver._probe_task is not None
    await saver._probe_task
    assert health.call_count == 1
    assert saver.health()["reachable"] is True
    recovered = await saver.transform(original)
    await saver.close()
    assert recovered.messages[0].content[0].text == "compressed"


@respx.mock
async def test_short_circuit_adds_no_latency():
    respx.post("http://localhost:8787/v1/compress").mock(side_effect=httpx.ConnectError("refused"))
    saver = HeadroomSaver()
    original = _req()
    await saver.transform(original)
    started = time.monotonic()
    for _ in range(50):
        await saver.transform(original)
    elapsed = time.monotonic() - started
    await saver.close()
    assert elapsed < 0.5


@respx.mock
async def test_pipeline_health_reports_async_savers():
    respx.get("http://localhost:8787/health").mock(side_effect=httpx.ConnectError("refused"))

    class Plain:
        async def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            return req

        async def close(self) -> None:
            pass

    pipeline = SaverPipeline([], [Plain(), HeadroomSaver()])
    health = await pipeline.health()
    await pipeline.close()
    assert list(health) == ["HeadroomSaver"]
    assert health["HeadroomSaver"]["reachable"] is False
