import base64
import gzip
import socket
from collections.abc import AsyncIterator

import httpx
import pytest

from janus.canonical.models import (
    CanonicalRequest,
    ImagePart,
    ImageSource,
    Message,
    Role,
    TextPart,
)
from janus.routing.modality import strip_unsupported_modalities
from janus.routing.prefetch import _MAX_IMAGE_BYTES, prefetch_remote_images


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.consumed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.consumed = True
        for chunk in self._chunks:
            yield chunk


def _resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    async def to_thread(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        del function, args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))]

    monkeypatch.setattr("janus.inventory.url_guard.asyncio.to_thread", to_thread)


def _image_part(req: CanonicalRequest) -> ImagePart:
    content = req.messages[0].content
    assert isinstance(content, list)
    return next(part for part in content if isinstance(part, ImagePart))


def _req_with_image(url: str = "https://img.example/a.png") -> CanonicalRequest:
    return CanonicalRequest(
        model="m",
        messages=[
            Message(
                role=Role.USER,
                content=[
                    TextPart(text="look"),
                    ImagePart(source=ImageSource(type="url", url=url)),
                ],
            )
        ],
    )


def test_strip_vision_when_unsupported():
    req = _req_with_image()
    out = strip_unsupported_modalities(req, {"vision": False})
    assert isinstance(out.messages[0].content, list)
    types = [getattr(p, "type", None) for p in out.messages[0].content]
    assert "image" not in types
    assert any(
        isinstance(p, TextPart) and "image omitted" in p.text for p in out.messages[0].content
    )


def test_strip_noop_when_vision_ok():
    req = _req_with_image()
    out = strip_unsupported_modalities(req, {"vision": True})
    assert out is req or any(isinstance(p, ImagePart) for p in out.messages[0].content)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_prefetch_inlines_valid_public_image(monkeypatch: pytest.MonkeyPatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=png,
            headers={"content-type": "image/png"},
            request=request,
        )

    req = _req_with_image()
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "93.184.216.34")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)
    part = _image_part(out)
    assert part.source.type == "base64"
    assert part.source.media_type == "image/png"
    assert part.source.data == base64.b64encode(png).decode("ascii")


@pytest.mark.asyncio
async def test_prefetch_rejects_direct_private_ip():
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=b"private", request=request)

    req = _req_with_image("http://127.0.0.1/private.png")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req
    assert requested is False


@pytest.mark.asyncio
async def test_prefetch_rejects_hostname_resolving_to_private_ip(
    monkeypatch: pytest.MonkeyPatch,
):
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=b"private", request=request)

    req = _req_with_image("https://internal.example/private.png")
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "10.0.0.8")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req
    assert requested is False


@pytest.mark.asyncio
async def test_prefetch_rejects_redirect_to_private_ip(monkeypatch: pytest.MonkeyPatch):
    requested_hosts: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append((request.url.host, request.headers["host"]))
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    req = _req_with_image("https://img.example/redirect")
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "93.184.216.34")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req
    assert requested_hosts == [("93.184.216.34", "img.example")]


@pytest.mark.asyncio
async def test_prefetch_ignores_private_base_url_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ALLOW_PRIVATE_BASE_URLS", "true")
    requested = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=b"metadata", request=request)

    req = _req_with_image("http://169.254.169.254/latest/meta-data")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req
    assert requested is False


@pytest.mark.asyncio
async def test_prefetch_pins_validated_dns_address(monkeypatch: pytest.MonkeyPatch):
    resolutions = 0

    async def to_thread(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        nonlocal resolutions
        del function, args, kwargs
        resolutions += 1
        address = "93.184.216.34" if resolutions == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))]

    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, content=b"image", request=request)

    req = _req_with_image()
    with monkeypatch.context() as dns_patch:
        dns_patch.setattr("janus.inventory.url_guard.asyncio.to_thread", to_thread)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    assert _image_part(out).source.type == "base64"
    assert resolutions == 1
    assert seen_request is not None
    assert seen_request.url.host == "93.184.216.34"
    assert seen_request.headers["host"] == "img.example"
    assert seen_request.headers["accept-encoding"] == "identity"
    assert seen_request.extensions["sni_hostname"] == "img.example"


@pytest.mark.asyncio
async def test_prefetch_rejects_oversized_content_length(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(_MAX_IMAGE_BYTES + 1)},
            content=b"x",
            request=request,
        )

    req = _req_with_image()
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "93.184.216.34")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req


@pytest.mark.asyncio
async def test_prefetch_rejects_compressed_response_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
):
    _resolve_to(monkeypatch, "93.184.216.34")
    compressed = gzip.compress(b"x" * (_MAX_IMAGE_BYTES + 1))
    stream = _ChunkStream([compressed])
    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            headers={
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            stream=stream,
            request=request,
        )

    req = _req_with_image()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req
    assert stream.consumed is False
    assert seen_headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_prefetch_rejects_oversized_chunked_body(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_ChunkStream([b"x" * _MAX_IMAGE_BYTES, b"y"]),
            request=request,
        )

    req = _req_with_image()
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "93.184.216.34")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    assert out is req


@pytest.mark.asyncio
async def test_prefetch_caps_total_bytes_across_images(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("janus.routing.prefetch._MAX_PREFETCH_BYTES", 5)
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"12345", request=request)

    req = CanonicalRequest(
        model="m",
        messages=[
            Message(
                role=Role.USER,
                content=[
                    ImagePart(source=ImageSource(type="url", url="https://img.example/a.png")),
                    ImagePart(source=ImageSource(type="url", url="https://img.example/b.png")),
                ],
            )
        ],
    )
    with monkeypatch.context() as dns_patch:
        _resolve_to(dns_patch, "93.184.216.34")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out = await prefetch_remote_images(req, "gemini", client=client)

    content = out.messages[0].content
    assert isinstance(content, list)
    images = [part for part in content if isinstance(part, ImagePart)]
    assert [part.source.type for part in images] == ["base64", "url"]
    assert requested_urls == ["https://93.184.216.34/a.png"]


@pytest.mark.asyncio
async def test_prefetch_noop_for_openai_target():
    req = _req_with_image()
    out = await prefetch_remote_images(req, "openai")
    assert out is req
