from __future__ import annotations

from typing import Protocol

from janus.canonical.models import CanonicalRequest


class TokenSaver(Protocol):
    def transform(self, req: CanonicalRequest) -> CanonicalRequest: ...
