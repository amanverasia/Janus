from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from janus.canonical.models import CanonicalRequest


class TokenSaver(Protocol):
    def transform(self, req: CanonicalRequest) -> CanonicalRequest: ...


class AsyncTokenSaver(Protocol):
    async def transform(self, req: CanonicalRequest) -> CanonicalRequest: ...

    async def close(self) -> None: ...


@runtime_checkable
class HealthCheckedSaver(AsyncTokenSaver, Protocol):
    async def check_health(self) -> dict[str, Any]: ...
