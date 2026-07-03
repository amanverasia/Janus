from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from janus.dashboard.auth import authenticate_dashboard_session
from janus.dashboard.credentials import (
    SESSION_COOKIE,
    SETTINGS_PASSWORD_HASH,
    SETTINGS_USERNAME,
    create_session_token,
    get_or_create_session_secret,
    hash_password,
)
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
