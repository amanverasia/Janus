from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request


def push_token_configured() -> bool:
    return bool(os.environ.get("INVENTORY_PUSH_TOKEN", "").strip())


async def require_inventory_push_token(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    expected = os.environ.get("INVENTORY_PUSH_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Inventory push token is not configured")
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing push token")
