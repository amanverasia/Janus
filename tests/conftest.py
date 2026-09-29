from __future__ import annotations

import asyncio

import pytest

from janus.dashboard import login_throttle
from janus.storage.database import close_connection_pools


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    login_throttle.reset_login_throttle()
    yield
    login_throttle.reset_login_throttle()


def pytest_sessionfinish(session: pytest.Session, exitstatus: pytest.ExitCode) -> None:
    asyncio.run(close_connection_pools())
