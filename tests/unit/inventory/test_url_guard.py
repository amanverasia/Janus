import socket

import httpx
import pytest
import respx

from janus.inventory.catalog import INVENTORY_PROVIDERS, get_inventory_provider
from janus.inventory.url_guard import (
    BlockedUrlError,
    assert_public_url,
    detect_provider_from_key,
    is_http_url,
    mask_key,
    safe_fetch,
)


def test_inventory_providers_count():
    from janus.catalog import inventory_catalog_entries

    assert INVENTORY_PROVIDERS == inventory_catalog_entries()
    assert "deepinfra" in INVENTORY_PROVIDERS


def test_get_inventory_provider():
    provider = get_inventory_provider("openrouter")
    assert provider is not None
    assert provider["credit_check_endpoint"] == "/key"


@pytest.mark.parametrize(
    ("key", "provider_id"),
    [
        ("sk-or-v1-abc", "openrouter"),
        ("sk-ant-abc", "anthropic"),
        ("nvapi-abc", "nvidia"),
        ("gsk_abc", "groq"),
        ("tvly-abc", "tavily"),
        ("fc-abc", "firecrawl"),
        ("BSAabc", "brave-search"),
        ("50945f2a-eeae-4555-ad99-a1b2c3d4e5f6:deadbeef", "fal"),
        ("0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOp", "zhipu"),
    ],
)
def test_detect_provider_from_key(key: str, provider_id: str):
    assert detect_provider_from_key(key) == provider_id


def test_mask_key():
    assert mask_key("short") == "****"
    assert mask_key("sk-or-v1-abcdefghijklmnop") == "sk-o****klmnop"


def test_is_http_url():
    assert is_http_url("https://api.openai.com/v1/models") is True
    assert is_http_url("file:///etc/passwd") is False


@pytest.mark.asyncio
async def test_assert_public_url_blocks_loopback():
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url("http://127.0.0.1/v1/models")


async def test_assert_public_url_allows_loopback_for_trusted_local_preset():
    await assert_public_url("http://127.0.0.1/v1/models", allow_private_network=True)


@pytest.mark.asyncio
async def test_assert_public_url_blocks_metadata_ip():
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url("http://169.254.169.254/latest/meta-data")


@pytest.mark.asyncio
async def test_assert_public_url_blocks_private_range():
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url("http://10.0.0.1/v1/models")


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["192.0.2.1", "198.51.100.1", "2001:db8::1"])
async def test_assert_public_url_blocks_reserved_ranges(address: str):
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url(f"http://[{address}]/" if ":" in address else f"http://{address}/")


@pytest.mark.asyncio
async def test_assert_public_url_can_ignore_private_environment_override(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ALLOW_PRIVATE_BASE_URLS", "true")
    await assert_public_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url(
            "http://169.254.169.254/latest/meta-data",
            respect_private_env=False,
        )


@pytest.mark.asyncio
async def test_assert_public_url_blocks_credentials():
    with pytest.raises(BlockedUrlError, match="Credentials"):
        await assert_public_url("https://user:pass@api.openai.com/v1/models")


@pytest.mark.asyncio
async def test_assert_public_url_blocks_non_http_scheme():
    with pytest.raises(BlockedUrlError, match="Blocked URL scheme"):
        await assert_public_url("ftp://example.com")


def _addr_info(address: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))]


@respx.mock
async def test_safe_fetch_pins_validated_address_and_resolves_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions: list[str] = []

    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object):
        resolutions.append(host)
        address = "93.184.216.34" if len(resolutions) == 1 else "10.0.0.1"
        return _addr_info(address)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    route = respx.get("https://93.184.216.34/v1/models").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    response = await safe_fetch("https://api.example.com/v1/models")

    assert response.status_code == 200
    assert route.called
    request = route.calls.last.request
    assert request.url.host == "93.184.216.34"
    assert request.headers["Host"] == "api.example.com"
    assert resolutions == ["api.example.com"]


async def test_safe_fetch_fails_closed_on_resolver_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[int, ...]]:
        raise OSError("resolver unavailable")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(BlockedUrlError, match="DNS resolution failed"):
        await safe_fetch("https://api.example.com/v1/models")


@respx.mock
async def test_safe_fetch_strips_credential_headers_on_cross_origin_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object):
        addresses = {"api.example.com": "93.184.216.34", "other.example.org": "8.8.8.8"}
        return _addr_info(addresses[host])

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    first = respx.get(
        "https://93.184.216.34/start",
        headers={"Host": "api.example.com"},
    ).mock(return_value=httpx.Response(302, headers={"Location": "https://other.example.org/next"}))
    second = respx.get(
        "https://8.8.8.8/next",
        headers={"Host": "other.example.org"},
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    response = await safe_fetch(
        "https://api.example.com/start",
        headers={
            "Authorization": "Bearer sk-secret",
            "X-Api-Key": "secondary-key",
            "Accept": "application/json",
        },
    )

    assert response.status_code == 200
    assert first.called
    assert second.called
    assert first.calls.last.request.headers["Authorization"] == "Bearer sk-secret"
    assert first.calls.last.request.headers["X-Api-Key"] == "secondary-key"
    assert "Authorization" not in second.calls.last.request.headers
    assert "X-Api-Key" not in second.calls.last.request.headers
    assert second.calls.last.request.headers["Accept"] == "application/json"


@respx.mock
async def test_safe_fetch_keeps_credential_headers_on_same_origin_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_info("93.184.216.34"))
    respx.get("https://93.184.216.34/start", headers={"Host": "api.example.com"}).mock(
        return_value=httpx.Response(302, headers={"Location": "https://api.example.com/next"})
    )
    second = respx.get("https://93.184.216.34/next", headers={"Host": "api.example.com"}).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await safe_fetch("https://api.example.com/start", headers={"Authorization": "Bearer sk-secret"})

    assert second.called
    assert second.calls.last.request.headers["Authorization"] == "Bearer sk-secret"


@respx.mock
async def test_safe_fetch_refuses_redirect_to_private_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_info("93.184.216.34"))
    route = respx.get("https://93.184.216.34/start", headers={"Host": "api.example.com"}).mock(
        return_value=httpx.Response(302, headers={"Location": "http://10.0.0.1/steal"})
    )

    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await safe_fetch(
            "https://api.example.com/start", headers={"Authorization": "Bearer sk-secret"}
        )

    assert route.called
    assert len(route.calls) == 1
