import pytest

from janus.storage.database import close_connection_pools


@pytest.fixture(autouse=True)
async def close_database_connections_after_test():
    yield
    await close_connection_pools()
