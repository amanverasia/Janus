from __future__ import annotations

from janus.canonical.models import CanonicalRequest, SystemBlock

PROMPTS: dict[str, str] = {
    "lite": (
        "Build what's asked. Prefer stdlib over new dependencies. "
        "Name the lazier alternative. Minimal diff."
    ),
    "full": (
        "Be a lazy senior developer. Deletion over addition. "
        "stdlib over new deps. One-liner over abstraction. "
        "Minimal code, minimal diff. Never add code that isn't requested."
    ),
    "ultra": (
        "YAGNI extremist. Deletion first. Ship the one-liner. "
        "Challenge unnecessary requirements in your response. "
        "The best code is no code. The second best is a one-liner. "
        "stdlib > native > existing deps > one-liner > minimal code."
    ),
}


class PonytailSaver:
    def __init__(self, level: str = "full") -> None:
        if level not in PROMPTS:
            raise ValueError(
                f"Invalid ponytail level: {level}. Must be one of: {list(PROMPTS.keys())}"
            )
        self.level = level

    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        req.system.insert(0, SystemBlock(type="text", text=PROMPTS[self.level]))
        return req
