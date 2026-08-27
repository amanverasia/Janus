"""Prefetch remote image URLs into base64 for providers that need inline data.

Ported from 9router ``open-sse/translator/concerns/prefetch.js``.
Runs on ``CanonicalRequest`` after parse, before build_upstream_request.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from janus.canonical.models import (
    CanonicalRequest,
    ContentPart,
    ImagePart,
    ImageSource,
    Message,
)
from janus.inventory.url_guard import MAX_REDIRECTS, BlockedUrlError, resolve_public_url

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
_MAX_PREFETCH_BYTES = 20 * 1024 * 1024
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


@dataclass
class _PrefetchBudget:
    remaining: int


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


def _pin_url(url: str, address: str) -> tuple[httpx.URL, dict[str, str], dict[str, Any]]:
    original = httpx.URL(url)
    host = original.host
    if host is None:
        raise BlockedUrlError(f"Invalid URL: {url}")
    extensions: dict[str, Any] = {}
    if original.scheme == "https":
        extensions["sni_hostname"] = host
    return (
        original.copy_with(host=address),
        {"Host": original.netloc.decode("ascii"), "Accept-Encoding": "identity"},
        extensions,
    )


async def _fetch_image(
    client: httpx.AsyncClient,
    url: str,
    budget: _PrefetchBudget,
) -> tuple[bytes, str] | None:
    current_url = url
    image_bytes_remaining = min(_MAX_IMAGE_BYTES, budget.remaining)
    for hop in range(MAX_REDIRECTS + 1):
        addresses = await resolve_public_url(current_url, respect_private_env=False)
        pinned_url, headers, extensions = _pin_url(current_url, addresses[0])
        async with client.stream(
            "GET",
            pinned_url,
            headers=headers,
            extensions=extensions,
            follow_redirects=False,
        ) as response:
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    return None
                if hop >= MAX_REDIRECTS:
                    raise BlockedUrlError(f"Too many redirects from {url}")
                current_url = str(httpx.URL(location, base=current_url))
                continue

            if response.status_code >= 400:
                return None

            content_encoding = response.headers.get("content-encoding", "").strip().lower()
            if content_encoding and content_encoding != "identity":
                return None

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > image_bytes_remaining:
                        return None
                except ValueError:
                    pass

            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > image_bytes_remaining:
                    budget.remaining = max(0, budget.remaining - len(chunk))
                    return None
                data.extend(chunk)
                image_bytes_remaining -= len(chunk)
                budget.remaining -= len(chunk)
            media_type = _guess_media_type(current_url, response.headers.get("content-type"))
            return bytes(data), media_type

    raise BlockedUrlError(f"Too many redirects from {url}")


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
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=False)
    try:
        new_messages: list[Message] = []
        changed = False
        converted = 0
        budget = _PrefetchBudget(remaining=_MAX_PREFETCH_BYTES)
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
                    if budget.remaining <= 0:
                        new_parts.append(part)
                        continue
                    fetched = await _fetch_image(client, url, budget)
                    if fetched is None:
                        new_parts.append(part)
                        continue
                    data, media_type = fetched
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
