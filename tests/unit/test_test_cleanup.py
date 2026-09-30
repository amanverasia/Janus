import subprocess
import sys
from pathlib import Path


def test_sqlite_workers_do_not_accumulate_between_tests(tmp_path):
    shared_conftest = Path(__file__).parents[1] / "conftest.py"
    (tmp_path / "conftest.py").write_text(shared_conftest.read_text())
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n")
    (tmp_path / "test_workers.py").write_text(
        """
import threading

import pytest

from janus.storage.database import get_connection


@pytest.mark.parametrize("iteration", range(12))
async def test_worker_is_released(iteration, tmp_path):
    workers = [
        thread for thread in threading.enumerate()
        if "_connection_worker_thread" in thread.name
    ]
    assert not workers, f"SQLite workers survived the previous test: {workers}"
    async with get_connection(tmp_path / "worker.db") as db:
        async with db.execute("SELECT 1") as cursor:
            assert (await cursor.fetchone())[0] == 1
"""
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12 passed" in result.stdout
