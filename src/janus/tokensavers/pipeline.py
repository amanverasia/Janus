from __future__ import annotations

import logging

from janus.canonical.models import CanonicalRequest

from .base import AsyncTokenSaver, TokenSaver

logger = logging.getLogger(__name__)


class SaverPipeline:
    def __init__(
        self,
        savers: list[TokenSaver],
        async_savers: list[AsyncTokenSaver] | None = None,
    ) -> None:
        self._savers = savers
        self._async_savers = async_savers or []

    def apply(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._savers:
            try:
                req = saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", type(saver).__name__, e)
        return req

    async def apply_async(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._async_savers:
            try:
                req = await saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", type(saver).__name__, e)
        return req

    async def close(self) -> None:
        for saver in self._async_savers:
            try:
                await saver.close()
            except Exception:
                pass
