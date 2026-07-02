from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_bound_app: FastAPI | None = None
_reload_task: asyncio.Task[None] | None = None


def bind_reload_app(app: FastAPI) -> None:
    global _bound_app
    _bound_app = app


async def reload_providers_for_db(db_path: str | Path) -> None:
    from janus.dashboard.reload import reload_providers

    if _bound_app is not None and _bound_app.state.db_path == db_path:
        await reload_providers(_bound_app)


def schedule_reload_providers(db_path: str | Path) -> None:
    global _reload_task

    async def _run() -> None:
        await asyncio.sleep(0.5)
        await reload_providers_for_db(db_path)

    if _reload_task is not None and not _reload_task.done():
        _reload_task.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _reload_task = loop.create_task(_run())
