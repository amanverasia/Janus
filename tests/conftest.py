import asyncio

import pytest

from janus.storage.database import close_connection_pools


def pytest_sessionfinish(session: pytest.Session, exitstatus: pytest.ExitCode) -> None:
    asyncio.run(close_connection_pools())
