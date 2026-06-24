from __future__ import annotations

import logging

from janus.canonical.models import CanonicalRequest

from .base import TokenSaver

logger = logging.getLogger(__name__)


class SaverPipeline:
    def __init__(self, savers: list[TokenSaver]) -> None:
        self._savers = savers

    def apply(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._savers:
            try:
                req = saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", type(saver).__name__, e)
        return req
