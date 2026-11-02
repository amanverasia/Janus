from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from janus.canonical.models import CanonicalRequest
from janus.formats.openai import OpenAIAdapter

logger = logging.getLogger(__name__)

DEFAULT_HEADROOM_URL = "http://localhost:8787"
HEALTH_TTL_S = 30.0
PROBE_TIMEOUT_S = 2.0

_openai = OpenAIAdapter()


class HeadroomSaver:
    """Optional external compression proxy (https://github.com/chopratejas/headroom).

    Sends the conversation to Headroom's `POST /v1/compress` endpoint and swaps
    in the compressed messages. Fails open: any error, timeout, or malformed
    response leaves the original request untouched.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_HEADROOM_URL,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        health_ttl: float = HEALTH_TTL_S,
        probe_timeout: float = PROBE_TIMEOUT_S,
    ) -> None:
        self._base_url = (base_url or DEFAULT_HEADROOM_URL).rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._health_ttl = health_ttl
        self._probe_timeout = probe_timeout
        self._reachable: bool | None = None
        self._reason: str | None = None
        self._checked_mono: float | None = None
        self._checked_at: str | None = None
        self._probe_task: asyncio.Task[None] | None = None

    def _mark(self, reachable: bool, reason: str | None) -> None:
        self._reachable = reachable
        self._reason = None if reachable else reason
        self._checked_mono = time.monotonic()
        self._checked_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _fresh(self) -> bool:
        return self._checked_mono is not None and (
            time.monotonic() - self._checked_mono < self._health_ttl
        )

    @staticmethod
    def _describe(exc: BaseException) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "Timed out waiting for Headroom"
        if isinstance(exc, httpx.ConnectError):
            return "Connection refused or host unreachable"
        if isinstance(exc, httpx.TransportError):
            return f"Network error ({type(exc).__name__})"
        return f"Probe failed ({type(exc).__name__})"

    async def _probe(self) -> None:
        try:
            response = await self._client.get(
                f"{self._base_url}/health", timeout=self._probe_timeout
            )
        except Exception as e:
            self._mark(False, self._describe(e))
            return
        if response.status_code >= 500:
            self._mark(False, f"Headroom returned HTTP {response.status_code}")
        else:
            self._mark(True, None)

    def _ensure_probe(self) -> asyncio.Task[None]:
        task = self._probe_task
        if task is None or task.done():
            task = asyncio.ensure_future(self._probe())
            self._probe_task = task
        return task

    async def check_health(self) -> dict[str, Any]:
        if not self._fresh():
            await asyncio.shield(self._ensure_probe())
        return self.health()

    def health(self) -> dict[str, Any]:
        return {
            "reachable": self._reachable,
            "reason": self._reason,
            "checked_at": self._checked_at,
        }

    def _short_circuit(self) -> bool:
        if self._reachable is not False:
            return False
        if not self._fresh():
            self._ensure_probe()
        return True

    async def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        if self._short_circuit():
            return req
        try:
            payload = _openai.build_upstream_request(
                req.model_copy(update={"stream": False}), req.model
            )
            response = await self._client.post(
                f"{self._base_url}/v1/compress",
                json={"model": req.model, "messages": payload["messages"]},
            )
            if response.status_code >= 500:
                self._mark(False, f"Headroom returned HTTP {response.status_code}")
            else:
                self._mark(True, None)
            if response.status_code != 200:
                logger.warning(
                    "Headroom compress returned %s; passing request through",
                    response.status_code,
                )
                return req
            data = response.json()
            messages = data.get("messages") if isinstance(data, dict) else None
            if not isinstance(messages, list) or not messages:
                return req
            parsed = _openai.parse_request({"model": req.model, "messages": messages})
            return req.model_copy(update={"system": parsed.system, "messages": parsed.messages})
        except Exception as e:
            if isinstance(e, httpx.TransportError):
                self._mark(False, self._describe(e))
            logger.warning("Headroom compress failed (%s); passing request through", e)
            return req

    async def close(self) -> None:
        task = self._probe_task
        if task is not None and not task.done():
            task.cancel()
        await self._client.aclose()
