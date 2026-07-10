import base64

import httpx
import pytest
import respx

from janus.canonical.models import (
    CanonicalRequest,
    ImagePart,
    ImageSource,
    Message,
    Role,
    TextPart,
)
from janus.routing.modality import strip_unsupported_modalities
from janus.routing.prefetch import prefetch_remote_images


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
@respx.mock
async def test_prefetch_inlines_remote_image():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    respx.get("https://img.example/a.png").mock(
        return_value=httpx.Response(200, content=png, headers={"content-type": "image/png"})
    )
    req = _req_with_image()
    out = await prefetch_remote_images(req, "gemini")
    part = next(p for p in out.messages[0].content if isinstance(p, ImagePart))  # type: ignore[union-attr]
    assert part.source.type == "base64"
    assert part.source.media_type == "image/png"
    assert part.source.data == base64.b64encode(png).decode("ascii")


@pytest.mark.asyncio
async def test_prefetch_noop_for_openai_target():
    req = _req_with_image()
    out = await prefetch_remote_images(req, "openai")
    assert out is req
