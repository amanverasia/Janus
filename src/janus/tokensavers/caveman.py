from __future__ import annotations

from janus.canonical.models import CanonicalRequest, SystemBlock

CAVEMAN_PROMPT = (
    "Respond with maximum brevity. Preserve technical substance. "
    "No pleasantries, no explanations of approach, no commentary. "
    "Just the answer. Why use many token when few token do trick."
)


class CavemanSaver:
    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        req.system.insert(0, SystemBlock(type="text", text=CAVEMAN_PROMPT))
        return req
