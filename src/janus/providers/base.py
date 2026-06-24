from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RawResult:
    status_code: int
    json_data: dict[str, Any] | None = None
    lines: AsyncIterator[str] | None = None


class Provider(Protocol):
    name: str

    async def call(self, payload: dict[str, Any], stream: bool) -> RawResult: ...
