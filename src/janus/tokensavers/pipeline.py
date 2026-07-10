from __future__ import annotations

import logging
from dataclasses import dataclass

from janus.canonical.models import CanonicalRequest

from .base import AsyncTokenSaver, TokenSaver

logger = logging.getLogger(__name__)


@dataclass
class SaverStats:
    name: str
    bytes_before: int
    bytes_after: int


def request_size(req: CanonicalRequest) -> int:
    """Cheap size probe: byte length of the JSON-serialized messages + system."""
    return len(req.model_dump_json(include={"messages", "system"}))


class SaverPipeline:
    def __init__(
        self,
        savers: list[TokenSaver],
        async_savers: list[AsyncTokenSaver] | None = None,
    ) -> None:
        self._savers = savers
        self._async_savers = async_savers or []
        # {saver_name: {"requests": n, "bytes_before": b, "bytes_after": a}}
        self.stats: dict[str, dict[str, int]] = {}

    def _record(self, name: str, bytes_before: int, bytes_after: int) -> None:
        try:
            entry = self.stats.setdefault(
                name, {"requests": 0, "bytes_before": 0, "bytes_after": 0}
            )
            entry["requests"] += 1
            entry["bytes_before"] += bytes_before
            entry["bytes_after"] += bytes_after
            if bytes_after < bytes_before:
                saved = bytes_before - bytes_after
                pct = (saved / bytes_before * 100) if bytes_before else 0.0
                logger.info("[%s] saved %d / %d bytes (%.1f%%)", name, saved, bytes_before, pct)
        except Exception as e:  # measurement must never break the pipeline
            logger.warning("Saver stats recording failed for %s: %s", name, e)

    def apply(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._savers:
            name = type(saver).__name__
            try:
                bytes_before = request_size(req)
            except Exception as e:
                logger.warning("Saver size probe failed for %s: %s", name, e)
                bytes_before = None
            try:
                req = saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", name, e)
                continue
            if bytes_before is not None:
                try:
                    bytes_after = request_size(req)
                    self._record(name, bytes_before, bytes_after)
                except Exception as e:
                    logger.warning("Saver size probe failed for %s: %s", name, e)
        return req

    async def apply_async(self, req: CanonicalRequest) -> CanonicalRequest:
        for saver in self._async_savers:
            name = type(saver).__name__
            try:
                bytes_before = request_size(req)
            except Exception as e:
                logger.warning("Saver size probe failed for %s: %s", name, e)
                bytes_before = None
            try:
                req = await saver.transform(req)
            except Exception as e:
                logger.warning("Token saver %s failed: %s", name, e)
                continue
            if bytes_before is not None:
                try:
                    bytes_after = request_size(req)
                    self._record(name, bytes_before, bytes_after)
                except Exception as e:
                    logger.warning("Saver size probe failed for %s: %s", name, e)
        return req

    def adopt_stats(self, other: SaverPipeline) -> None:
        """Carry cumulative in-memory savings counters across pipeline rebuilds."""
        for name, counters in other.stats.items():
            entry = self.stats.setdefault(
                name, {"requests": 0, "bytes_before": 0, "bytes_after": 0}
            )
            entry["requests"] += counters.get("requests", 0)
            entry["bytes_before"] += counters.get("bytes_before", 0)
            entry["bytes_after"] += counters.get("bytes_after", 0)

    async def close(self) -> None:
        for saver in self._async_savers:
            try:
                await saver.close()
            except Exception:
                pass
