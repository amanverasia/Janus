from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, Query, Request

from janus.api.auth import authenticate_api_key, extract_api_key, is_require_api_key_enabled
from janus.api.rate_limit import GatewayRateLimiter
from janus.storage.settings import get_all_settings, resolve_gateway_rate_limit_rpm

logger = logging.getLogger(__name__)


async def require_api_key(
    request: Request,
    authorization: str = Header(default=""),
    x_goog_api_key: str = Header(default="", alias="x-goog-api-key"),
    key_query: str = Query(default="", alias="key"),
) -> None:
    key = extract_api_key(request, authorization, x_goog_api_key, key_query)
    if key:
        await authenticate_api_key(request, key)

    if not await is_require_api_key_enabled(request):
        return

    if not key or not (
        getattr(request.state, "client_key_id", None) is not None
        or getattr(request.state, "client_key_label", None) is not None
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


def _rate_limit_identity(request: Request) -> str:
    key_id = getattr(request.state, "client_key_id", None)
    if key_id is not None:
        return f"key:{key_id}"
    config_identity = getattr(request.state, "client_key_identity", None)
    if config_identity:
        return f"config:{config_identity}"
    if request.client and request.client.host:
        return f"ip:{request.client.host}"
    return "ip:unknown"


async def require_gateway_rate_limit(
    request: Request,
    _: None = Depends(require_api_key),
) -> None:
    del _
    try:
        settings = await get_all_settings(request.app.state.db_path)
        limit = resolve_gateway_rate_limit_rpm(settings)
        if limit <= 0:
            return
        limiter: GatewayRateLimiter = request.app.state.gateway_rate_limiter
        result = limiter.check(_rate_limit_identity(request), limit)
    except Exception as exc:
        logger.warning("Gateway rate limit check failed, allowing request: %s", exc, exc_info=True)
        return

    if result.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail={
            "error": {
                "message": f"Gateway rate limit exceeded. Try again in {result.retry_after}s.",
                "type": "rate_limit_exceeded",
                "retry_after_seconds": result.retry_after,
            }
        },
        headers={
            "Retry-After": str(result.retry_after),
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
            "X-RateLimit-Reset": str(result.reset_at),
        },
    )
