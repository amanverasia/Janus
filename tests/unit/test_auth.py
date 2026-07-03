from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.datastructures import Address

from janus.api.auth import (
    authenticate_api_key,
    extract_api_key,
    is_require_api_key_enabled,
    is_trusted_dashboard_client,
)
from janus.config.schema import JanusConfig, ServerSettings


def _request(
    *,
    host: str | None = "127.0.0.1",
    config: JanusConfig | None = None,
    cookies: dict[str, str] | None = None,
) -> MagicMock:
    req = MagicMock()
    req.app.state.config = config or JanusConfig()
    req.app.state.db_path = config.server.data_dir / "janus.db" if config else MagicMock()
    req.state = MagicMock()
    req.cookies = cookies or {}
    if host is None:
        req.client = None
    else:
        req.client = Address(host, 12345)
    return req


def test_extract_api_key_bearer() -> None:
    req = _request()
    assert extract_api_key(req, "Bearer sk-test", "", "") == "sk-test"


def test_extract_api_key_cookie() -> None:
    req = _request(cookies={"janus_dashboard_key": "sk-janus-abc"})
    assert extract_api_key(req, "", "", "") == "sk-janus-abc"


def test_is_trusted_loopback() -> None:
    assert is_trusted_dashboard_client(_request(host="127.0.0.1")) is True
    assert is_trusted_dashboard_client(_request(host="::1")) is True
    assert is_trusted_dashboard_client(_request(host="testclient")) is True
    assert is_trusted_dashboard_client(_request(host=None)) is True


def test_is_trusted_remote() -> None:
    assert is_trusted_dashboard_client(_request(host="192.168.1.10")) is False


@pytest.mark.asyncio
async def test_is_require_api_key_from_db(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.settings import set_setting

    cfg = JanusConfig(server=ServerSettings(require_api_key=False, data_dir=tmp_path))
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, "server_require_api_key", "true")
    req = _request(config=cfg)
    req.app.state.db_path = db_path
    assert await is_require_api_key_enabled(req) is True


@pytest.mark.asyncio
async def test_is_require_api_key_falls_back_to_config(tmp_path) -> None:
    from janus.storage.database import init_db

    cfg = JanusConfig(server=ServerSettings(require_api_key=True, data_dir=tmp_path))
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    req = _request(config=cfg)
    req.app.state.db_path = db_path
    assert await is_require_api_key_enabled(req) is True


@pytest.mark.asyncio
async def test_authenticate_static_yaml_key_sets_label(tmp_path) -> None:
    cfg = JanusConfig(
        server=ServerSettings(data_dir=tmp_path),
        api_keys=["sk-static"],
    )
    req = _request(config=cfg)
    req.app.state.db_path = tmp_path / "janus.db"
    assert await authenticate_api_key(req, "sk-static") is True
    assert req.state.client_key_label.startswith("Config (")


@pytest.mark.asyncio
async def test_authenticate_db_key(tmp_path) -> None:
    from janus.storage.api_keys import create_key
    from janus.storage.database import init_db

    cfg = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "test")
    req = _request(config=cfg)
    req.app.state.db_path = db_path
    assert await authenticate_api_key(req, full_key) is True
