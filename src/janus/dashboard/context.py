from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from janus.dashboard.alerts import collect_dashboard_alerts


async def dashboard_context(request: Request, db_path: Path, **extra: Any) -> dict[str, Any]:
    alert_data = await collect_dashboard_alerts(db_path, request)
    return {
        "request": request,
        "global_alerts": alert_data["alerts"],
        "alert_summary": alert_data["summary"],
        "alert_counts": alert_data["counts"],
        **extra,
    }
