from __future__ import annotations

import pytest

from janus.dashboard import login_throttle


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    login_throttle.reset_login_throttle()
    yield
    login_throttle.reset_login_throttle()
