from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import httpx

MAX_REDIRECTS = 3

_BLOCKED_V4: list[tuple[str, int]] = [
    ("0.0.0.0", 8),
    ("10.0.0.0", 8),
    ("100.64.0.0", 10),
    ("127.0.0.0", 8),
    ("169.254.0.0", 16),
    ("172.16.0.0", 12),
    ("192.0.0.0", 24),
    ("192.168.0.0", 16),
    ("198.18.0.0", 15),
    ("224.0.0.0", 4),
    ("240.0.0.0", 4),
    ("255.255.255.255", 32),
]


class BlockedUrlError(ValueError):
    pass


def _allow_private() -> bool:
    return os.environ.get("ALLOW_PRIVATE_BASE_URLS", "").lower() == "true"


def _ipv4_to_int(ip: str) -> int | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    value = 0
    for part in parts:
        if not part.isdigit():
            return None
        octet = int(part)
        if octet < 0 or octet > 255:
            return None
        if len(part) > 1 and part[0] == "0":
            return None
        value = value * 256 + octet
    return value


def _in_v4_cidr(ip_int: int, base: str, bits: int) -> bool:
    base_int = _ipv4_to_int(base)
    if base_int is None:
        return False
    if bits == 0:
        return True
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    return (ip_int & mask) == (base_int & mask)


def _extract_embedded_v4(addr: str) -> str | None:
    last_colon = addr.rfind(":")
    if last_colon != -1:
        tail = addr[last_colon + 1 :]
        if "." in tail:
            try:
                ipaddress.IPv4Address(tail)
            except ValueError:
                pass
            else:
                return tail

    if addr.startswith("::ffff:") or addr.startswith("64:ff9b:"):
        groups = [group for group in addr.split(":") if group]
        if len(groups) >= 2:
            try:
                hi = int(groups[-2], 16)
                lo = int(groups[-1], 16)
            except ValueError:
                return None
            if hi <= 0xFFFF and lo <= 0xFFFF:
                return f"{(hi >> 8) & 0xFF}.{hi & 0xFF}.{(lo >> 8) & 0xFF}.{lo & 0xFF}"
    return None


def _is_blocked_v4(ip: str) -> bool:
    ip_int = _ipv4_to_int(ip)
    if ip_int is None:
        return True
    return any(_in_v4_cidr(ip_int, base, bits) for base, bits in _BLOCKED_V4)


def _is_blocked_v6(ip: str) -> bool:
    addr = ip.lower()
    percent = addr.find("%")
    if percent != -1:
        addr = addr[:percent]

    embedded = _extract_embedded_v4(addr)
    if embedded is not None:
        return _is_blocked_v4(embedded)

    if addr in {"::1", "::"}:
        return True
    if addr.startswith(("fe8", "fe9", "fea", "feb")):
        return True
    if addr.startswith(("fc", "fd")):
        return True
    if addr.startswith("ff"):
        return True
    return False


def _is_blocked_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(parsed, ipaddress.IPv4Address):
        return _is_blocked_v4(str(parsed))
    return _is_blocked_v6(str(parsed))


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"}


async def assert_public_url(raw_url: str) -> None:
    try:
        parsed = httpx.URL(raw_url)
    except Exception as exc:
        raise BlockedUrlError(f"Invalid URL: {raw_url}") from exc

    if parsed.scheme not in {"http", "https"}:
        raise BlockedUrlError(f"Blocked URL scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise BlockedUrlError("Credentials in URL are not allowed")

    if _allow_private():
        return

    host = parsed.host
    if host is None:
        raise BlockedUrlError(f"Invalid URL: {raw_url}")

    host = host.strip("[]")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_blocked_ip(host):
            raise BlockedUrlError(f"Blocked address: {host}")
        return

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise BlockedUrlError(f"DNS resolution failed for {host}: {exc}") from exc

    if not addresses:
        raise BlockedUrlError(f"No addresses for {host}")

    for info in addresses:
        sockaddr = info[4]
        if sockaddr is None:
            continue
        address = str(sockaddr[0])
        if _is_blocked_ip(address):
            raise BlockedUrlError(f"Host {host} resolves to blocked address {address}")


def _resolve_redirect(current_url: str, location: str) -> str:
    return str(httpx.URL(location, base=current_url))


async def safe_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    current_url = url
    current_method = method.upper()
    current_content = content

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            await assert_public_url(current_url)
            response = await client.request(
                current_method,
                current_url,
                headers=headers,
                content=current_content,
            )

            if response.status_code not in {301, 302, 303, 307, 308}:
                return response

            location = response.headers.get("location")
            if not location:
                return response

            if hop >= MAX_REDIRECTS:
                raise BlockedUrlError(f"Too many redirects from {url}")

            current_url = _resolve_redirect(current_url, location)
            if response.status_code in {301, 302, 303}:
                current_method = "GET"
                current_content = None

    raise BlockedUrlError(f"Too many redirects from {url}")


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-6:]}"


_FAL_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]+$",
    re.IGNORECASE,
)
_ZHIPU_KEY_RE = re.compile(r"^[0-9a-f]{32}\.[A-Za-z0-9]{16}$")


def detect_provider_from_key(key: str) -> str | None:
    if key.startswith("sk-or-v1-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("nvapi-"):
        return "nvidia"
    if key.startswith("sk-proj-"):
        return "openai"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("xai-"):
        return "xai"
    if key.startswith("hf_"):
        return "huggingface"
    if key.startswith("r8_"):
        return "replicate"
    if key.startswith("pplx-"):
        return "perplexity"
    if key.startswith("fw_"):
        return "fireworks"
    if key.startswith("sk-cp-"):
        return "minimax"
    if key.startswith("tp-"):
        return "xiaomi_tokenplan"
    if key.startswith("tvly-"):
        return "tavily"
    if key.startswith("fc-"):
        return "firecrawl"
    if key.startswith("BSA"):
        return "brave-search"
    if _FAL_KEY_RE.match(key):
        return "fal"
    if _ZHIPU_KEY_RE.match(key):
        return "zhipu"
    if key.startswith("sk-") and not key.startswith(("sk-or", "sk-ant")):
        return "openai"
    return None
