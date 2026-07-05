from __future__ import annotations

import logging

import httpx

from janus.canonical.models import CanonicalRequest
from janus.formats.openai import OpenAIAdapter

logger = logging.getLogger(__name__)

DEFAULT_HEADROOM_URL = "http://localhost:8787"

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
    ) -> None:
        self._base_url = (base_url or DEFAULT_HEADROOM_URL).rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        try:
            payload = _openai.build_upstream_request(
                req.model_copy(update={"stream": False}), req.model
            )
            response = await self._client.post(
                f"{self._base_url}/v1/compress",
                json={"model": req.model, "messages": payload["messages"]},
            )
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
            logger.warning("Headroom compress failed (%s); passing request through", e)
            return req

    async def close(self) -> None:
        await self._client.aclose()
