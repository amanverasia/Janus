import pytest

from janus.inventory.catalog import INVENTORY_PROVIDERS, get_inventory_provider
from janus.inventory.url_guard import (
    BlockedUrlError,
    assert_public_url,
    detect_provider_from_key,
    is_http_url,
    mask_key,
)


def test_inventory_providers_count():
    assert len(INVENTORY_PROVIDERS) == 29


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


@pytest.mark.asyncio
async def test_assert_public_url_blocks_metadata_ip():
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url("http://169.254.169.254/latest/meta-data")


@pytest.mark.asyncio
async def test_assert_public_url_blocks_private_range():
    with pytest.raises(BlockedUrlError, match="Blocked address"):
        await assert_public_url("http://10.0.0.1/v1/models")


@pytest.mark.asyncio
async def test_assert_public_url_blocks_credentials():
    with pytest.raises(BlockedUrlError, match="Credentials"):
        await assert_public_url("https://user:pass@api.openai.com/v1/models")


@pytest.mark.asyncio
async def test_assert_public_url_blocks_non_http_scheme():
    with pytest.raises(BlockedUrlError, match="Blocked URL scheme"):
        await assert_public_url("ftp://example.com")
