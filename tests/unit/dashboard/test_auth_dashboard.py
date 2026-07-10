from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Address

from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.auth import authenticate_dashboard_session, require_dashboard_access
from janus.dashboard.credentials import (
    SESSION_COOKIE,
    SETTINGS_PASSWORD_HASH,
    SETTINGS_USERNAME,
    create_session_token,
    get_or_create_session_secret,
    hash_password,
)
from janus.storage.api_keys import create_key
from janus.storage.database import init_db
from janus.storage.settings import set_setting


def _request(*, cookies: dict[str, str], db_path) -> MagicMock:
    req = MagicMock()
    req.app.state.db_path = db_path
    req.cookies = cookies
    return req


@pytest.mark.asyncio
async def test_authenticate_dashboard_session_valid(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, SETTINGS_USERNAME, "admin")
    await set_setting(db_path, SETTINGS_PASSWORD_HASH, hash_password("pw"))
    secret = await get_or_create_session_secret(db_path)
    token = create_session_token(secret, "admin")
    req = _request(cookies={SESSION_COOKIE: token}, db_path=db_path)
    assert await authenticate_dashboard_session(req) is True


@pytest.mark.asyncio
async def test_authenticate_dashboard_session_invalid(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await set_setting(db_path, SETTINGS_USERNAME, "admin")
    req = _request(cookies={SESSION_COOKIE: "bad-token"}, db_path=db_path)
    assert await authenticate_dashboard_session(req) is False


@pytest.mark.asyncio
async def test_require_dashboard_rejects_api_only_key(tmp_path) -> None:
    cfg = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "api-only", can_login=False)

    req = MagicMock()
    req.app.state.config = cfg
    req.app.state.db_path = db_path
    req.state = MagicMock()
    req.cookies = {}
    req.url.path = "/dashboard"
    req.headers = {"accept": "application/json"}
    req.method = "POST"
    req.client = Address("192.168.1.10", 12345)

    with pytest.raises(HTTPException) as exc:
        await require_dashboard_access(req, authorization=f"Bearer {full_key}")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_dashboard_allows_login_key(tmp_path) -> None:
    cfg = JanusConfig(server=ServerSettings(data_dir=tmp_path))
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "admin-key", can_login=True)

    req = MagicMock()
    req.app.state.config = cfg
    req.app.state.db_path = db_path
    req.state = MagicMock()
    req.cookies = {}
    req.url.path = "/dashboard"
    req.headers = {"accept": "application/json"}
    req.method = "GET"
    req.client = Address("192.168.1.10", 12345)

    await require_dashboard_access(req, authorization=f"Bearer {full_key}")
