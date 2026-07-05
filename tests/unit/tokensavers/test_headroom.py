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
