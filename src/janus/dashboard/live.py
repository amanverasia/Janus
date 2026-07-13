"""In-memory live-usage bus powering the Usage tab's real-time activity view.

Ported in spirit from 9router's ``/api/usage/stream`` SSE feed (its React Flow
provider topology is driven by an in-memory pending-request tracker with a
recent-requests ring buffer). Janus keeps the same architecture — in-flight
gauge + completed-request ring, pushed over SSE — but renders it as a rolling
requests-per-second chart and a live feed instead of a node graph.

Everything here is process-local and fail-open: the bus must never affect
request handling. Events are dropped, never awaited on, when subscribers lag.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any

RING_CAP = 50
SUBSCRIBER_QUEUE_CAP = 100

_bus: LiveUsageBus | None = None


def get_bus() -> LiveUsageBus:
    """Process-wide bus singleton.

    Module-level (not app.state) so the storage layer can notify completions
    without holding an app reference; tests reset it via ``reset_bus``.
    """
    global _bus
    if _bus is None:
        _bus = LiveUsageBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None


class LiveUsageBus:
    def __init__(self) -> None:
        self._in_flight: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._recent: deque[dict[str, Any]] = deque(maxlen=RING_CAP)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    # ── request lifecycle (called from the ASGI middleware) ──────────

    def begin(self, path: str) -> int:
        rid = next(self._ids)
        self._in_flight[rid] = {"path": path, "started": time.time()}
        self._publish({"type": "inflight", "count": len(self._in_flight)})
        return rid

    def end(self, rid: int) -> None:
        if self._in_flight.pop(rid, None) is not None:
            self._publish({"type": "inflight", "count": len(self._in_flight)})

    # ── completed usage (called via the record_usage hook) ───────────

    def record_completed(self, **fields: Any) -> None:
        event = {
            "type": "request",
            "ts": time.time(),
            "model": fields.get("model"),
            "provider_id": fields.get("provider_id"),
            "user": fields.get("client_key_label")
            or (f"key #{fields['client_key_id']}" if fields.get("client_key_id") else None),
            "input_tokens": fields.get("input_tokens") or 0,
            "output_tokens": fields.get("output_tokens") or 0,
            "cost": fields.get("cost") or 0.0,
            "status": fields.get("status"),
        }
        self._recent.append(event)
        self._publish(event)

    # ── SSE plumbing ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "inflight": len(self._in_flight),
            "recent": list(self._recent),
        }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_CAP)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def _publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop the event rather than block requests.
                pass


class LiveTrackingMiddleware:
    """Pure ASGI middleware counting in-flight gateway requests.

    Tracks /v1, /v1beta, and /api (Ollama) traffic. The request is considered
    finished when the final response body message is sent, so streaming
    responses stay "in flight" until the stream actually completes.
    """

    _PREFIXES = ("/v1/", "/v1beta/", "/api/")

    def __init__(self, app: Any, bus: LiveUsageBus) -> None:
        self.app = app
        self.bus = bus

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(self._PREFIXES):
            await self.app(scope, receive, send)
            return
        rid = self.bus.begin(scope["path"])
        done = False

        async def wrapped_send(message: Any) -> None:
            nonlocal done
            await send(message)
            if (
                not done
                and message.get("type") == "http.response.body"
                and not message.get("more_body", False)
            ):
                done = True
                self.bus.end(rid)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            if not done:
                self.bus.end(rid)
