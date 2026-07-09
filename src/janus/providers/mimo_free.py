from __future__ import annotations

import hashlib
import os
import random
import secrets
import string
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

BOOTSTRAP_URL = "https://api.xiaomimimo.com/api/free-ai/bootstrap"
CHAT_URL = "https://api.xiaomimimo.com/api/free-ai/openai/chat"
SESSION_AFFINITY_PREFIX = "ses_"
SESSION_ID_LENGTH = 24
JWT_FALLBACK_TTL_SEC = 3000
JWT_EXPIRY_BUFFER_MS = 300_000

MIMO_SYSTEM_MARKER = (
    "You are MiMoCode, an interactive CLI tool that helps users with software engineering tasks."
)

_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

_SESSION_CHARS = string.ascii_lowercase + string.digits


def generate_fingerprint() -> str:
    try:
        username = os.getlogin()
    except OSError:
        username = "unknown-user"
    seed = f"{os.uname().nodename}|{os.name}|{os.uname().machine}|{username}"
    return hashlib.sha256(seed.encode()).hexdigest()


def generate_session_id() -> str:
    body = "".join(secrets.choice(_SESSION_CHARS) for _ in range(SESSION_ID_LENGTH))
    return f"{SESSION_AFFINITY_PREFIX}{body}"


def parse_jwt_exp_ms(jwt: str) -> int:
    try:
        import base64
        import json

        payload_b64 = jwt.split(".")[1]
        pad = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp * 1000)
    except Exception:
        pass
    return int(time.time() * 1000) + JWT_FALLBACK_TTL_SEC * 1000


def inject_system_marker(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    for msg in messages:
        if (
            isinstance(msg, dict)
            and msg.get("role") == "system"
            and isinstance(msg.get("content"), str)
            and MIMO_SYSTEM_MARKER in msg["content"]
        ):
            return payload
    return {
        **payload,
        "messages": [{"role": "system", "content": MIMO_SYSTEM_MARKER}, *messages],
    }


class MimoFreeProvider:
    """Xiaomi MiMo Code Free — bootstrap JWT + anti-abuse headers (9router parity)."""

    name = "mimo_free"

    def __init__(self) -> None:
        self.base_url = CHAT_URL
        self.api_key: str | None = None
        self.session_id = generate_session_id()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)
        self._jwt: str | None = None
        self._jwt_expires_at_ms: int = 0
        self._fingerprint = generate_fingerprint()

    def _reset_jwt(self) -> None:
        self._jwt = None
        self._jwt_expires_at_ms = 0

    async def _bootstrap_jwt(self) -> str:
        now_ms = int(time.time() * 1000)
        if self._jwt and now_ms < self._jwt_expires_at_ms - JWT_EXPIRY_BUFFER_MS:
            return self._jwt
        r = await self._client.post(
            BOOTSTRAP_URL,
            json={"client": self._fingerprint},
            headers={
                "Content-Type": "application/json",
                "User-Agent": random.choice(_USER_AGENTS),
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(f"MiMo bootstrap failed: {r.status_code}")
        data = r.json()
        jwt = data.get("jwt") if isinstance(data, dict) else None
        if not isinstance(jwt, str) or not jwt:
            raise RuntimeError("MiMo bootstrap returned no JWT")
        self._jwt = jwt
        self._jwt_expires_at_ms = parse_jwt_exp_ms(jwt)
        return jwt

    def _headers(self, jwt: str, *, stream: bool) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt}",
            "X-Mimo-Source": "mimocode-cli-free",
            "User-Agent": random.choice(_USER_AGENTS),
            "x-session-affinity": self.session_id,
            "Accept": "text/event-stream" if stream else "application/json",
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        body = inject_system_marker(payload)
        try:
            jwt = await self._bootstrap_jwt()
        except RuntimeError as exc:
            return RawResult(status_code=502, json_data={"error": {"message": str(exc)}})

        if stream:
            return await self._call_stream(body, jwt)

        r = await self._client.post(CHAT_URL, json=body, headers=self._headers(jwt, stream=False))
        if r.status_code in (401, 403):
            self._reset_jwt()
            try:
                jwt = await self._bootstrap_jwt()
            except RuntimeError as exc:
                return RawResult(status_code=502, json_data={"error": {"message": str(exc)}})
            r = await self._client.post(
                CHAT_URL, json=body, headers=self._headers(jwt, stream=False)
            )
        if r.status_code >= 400:
            return RawResult(
                status_code=r.status_code,
                json_data=r.json() if r.content else {"error": {"message": r.text}},
                retry_after=parse_retry_after(r.headers),
            )
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, payload: dict[str, Any], jwt: str) -> RawResult:
        payload = {**payload, "stream": True}
        headers = self._headers(jwt, stream=True)
        cm = self._client.stream("POST", CHAT_URL, json=payload, headers=headers)
        r = await cm.__aenter__()
        if r.status_code in (401, 403):
            body = await r.aread()
            await cm.__aexit__(None, None, None)
            self._reset_jwt()
            try:
                jwt = await self._bootstrap_jwt()
            except RuntimeError as exc:
                return RawResult(status_code=502, json_data={"error": {"message": str(exc)}})
            headers = self._headers(jwt, stream=True)
            cm = self._client.stream("POST", CHAT_URL, json=payload, headers=headers)
            r = await cm.__aenter__()
        if r.status_code >= 400:
            body = await r.aread()
            await cm.__aexit__(None, None, None)
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(body),
                retry_after=parse_retry_after(r.headers),
            )

        async def line_iter() -> AsyncIterator[str]:
            try:
                async for raw_line in r.aiter_lines():
                    yield raw_line
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
