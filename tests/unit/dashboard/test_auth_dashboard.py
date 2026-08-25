from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Address

from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.auth import require_dashboard_access
from janus.storage.api_keys import create_key
from janus.storage.database import init_db
from janus.storage.settings import set_setting


def _request(
    *,
    db_path,
    cookies: dict[str, str] | None = None,
    method: str = "GET",
    accept: str = "text/html",
    client_host: str = "192.0.2.10",
) -> MagicMock:
    request = MagicMock()
    request.app.state = SimpleNamespace(
        config=JanusConfig(server=ServerSettings(data_dir=db_path.parent)),
        db_path=db_path,
    )
    request.state = SimpleNamespace()
    request.cookies = cookies or {}
    request.url.path = "/dashboard"
    request.headers = {"accept": accept}
    request.method = method
    request.client = Address(client_host, 12345)
    return request


def _legacy_session_token(secret: str, username: str) -> str:
    expires_at = int(time.time()) + 3600
    payload = f"{username}:{expires_at}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


@pytest.mark.asyncio
async def test_require_dashboard_rejects_api_only_key(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "api-only", can_login=False)
    request = _request(db_path=db_path, method="POST", accept="application/json")

    with pytest.raises(HTTPException) as exc:
        await require_dashboard_access(
            request,
            authorization=f"Bearer {full_key}",
            x_goog_api_key="",
            key_query="",
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_dashboard_allows_login_key_in_authorization_header(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "admin-key", can_login=True)
    request = _request(db_path=db_path)

    await require_dashboard_access(
        request,
        authorization=f"Bearer {full_key}",
        x_goog_api_key="",
        key_query="",
    )


@pytest.mark.asyncio
async def test_require_dashboard_allows_login_key_cookie(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    full_key, _ = await create_key(db_path, "admin-key", can_login=True)
    request = _request(db_path=db_path, cookies={"janus_dashboard_key": full_key})

    await require_dashboard_access(request, authorization="", x_goog_api_key="", key_query="")


@pytest.mark.asyncio
async def test_require_dashboard_rejects_unauthenticated_loopback_client(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    request = _request(db_path=db_path, client_host="127.0.0.1")

    with pytest.raises(HTTPException) as exc:
        await require_dashboard_access(request, authorization="", x_goog_api_key="", key_query="")
    assert exc.value.status_code == 303
    assert exc.value.headers is not None
    assert exc.value.headers["Location"].startswith("/dashboard/login")


@pytest.mark.asyncio
async def test_require_dashboard_rejects_valid_legacy_session_cookie(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    secret = "legacy-session-secret"
    await set_setting(db_path, "dashboard_username", "hett")
    await set_setting(db_path, "dashboard_session_secret", secret)
    token = _legacy_session_token(secret, "hett")
    request = _request(
        db_path=db_path,
        cookies={"janus_dashboard_session": token},
    )

    with pytest.raises(HTTPException) as exc:
        await require_dashboard_access(request, authorization="", x_goog_api_key="", key_query="")
    assert exc.value.status_code == 303
