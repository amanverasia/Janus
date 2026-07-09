"""Prefetch remote image URLs into base64 for providers that need inline data.

Ported from 9router ``open-sse/translator/concerns/prefetch.js``.
Runs on ``CanonicalRequest`` after parse, before build_upstream_request.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import urlparse

import httpx

from janus.canonical.models import (
    CanonicalRequest,
    ContentPart,
    ImagePart,
    ImageSource,
    Message,
)

logger = logging.getLogger(__name__)

TARGETS_NEED_BASE64: frozenset[str] = frozenset(
    {
        "gemini",
        "ollama",
        "antigravity",
        "kiro",
        "vertex",
    }
)

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)


def _is_remote_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    return url.startswith("http://") or url.startswith("https://")


def _guess_media_type(url: str, content_type: str | None) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type.split(";")[0].strip()
    path = urlparse(url).path.lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


async def prefetch_remote_images(
    req: CanonicalRequest,
    target_format: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> CanonicalRequest:
    """Inline remote image URLs when the target format cannot fetch them."""
    if target_format not in TARGETS_NEED_BASE64:
        return req

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        new_messages: list[Message] = []
        changed = False
        converted = 0
        for msg in req.messages:
            if not isinstance(msg.content, list):
                new_messages.append(msg)
                continue
            new_parts: list[ContentPart] = []
            msg_changed = False
            for part in msg.content:
                if not isinstance(part, ImagePart) or part.source.type != "url":
                    new_parts.append(part)
                    continue
                url = part.source.url
                if not _is_remote_url(url):
                    new_parts.append(part)
                    continue
                assert url is not None
                try:
                    r = await client.get(url)
                    if r.status_code >= 400:
                        new_parts.append(part)
                        continue
                    data = r.content
                    if len(data) > _MAX_IMAGE_BYTES:
                        new_parts.append(part)
                        continue
                    media_type = _guess_media_type(url, r.headers.get("content-type"))
                    b64 = base64.b64encode(data).decode("ascii")
                    new_parts.append(
                        ImagePart(
                            source=ImageSource(
                                type="base64",
                                media_type=media_type,
                                data=b64,
                            )
                        )
                    )
                    msg_changed = True
                    converted += 1
                except Exception as e:
                    logger.debug("Image prefetch failed for %s: %s", url, e)
                    new_parts.append(part)
            if msg_changed:
                changed = True
                new_messages.append(msg.model_copy(update={"content": new_parts}))
            else:
                new_messages.append(msg)
        if converted:
            logger.debug("Prefetched %d remote image(s) for %s", converted, target_format)
        if not changed:
            return req
        return req.model_copy(update={"messages": new_messages})
    finally:
        if owns_client:
            await client.aclose()


def target_needs_base64_images(target_format: str) -> bool:
    return target_format in TARGETS_NEED_BASE64
