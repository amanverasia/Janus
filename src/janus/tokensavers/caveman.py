from __future__ import annotations

from janus.canonical.models import CanonicalRequest, SystemBlock

SAFETY_BOUNDARIES = (
    "Write normally for security warnings, irreversible-action confirmations, "
    "and multi-step instructions; preserve the user's language; never abbreviate "
    "code, paths, commands, error messages, or URLs."
)

PROMPTS: dict[str, str] = {
    "lite": (
        "Be brief. Skip pleasantries and skip explaining your approach. "
        "Keep code, paths, commands, error messages, and URLs exact — never abbreviate them."
    ),
    "full": (
        "Respond with maximum brevity. Preserve technical substance. "
        "No pleasantries, no explanations of approach, no commentary. "
        "Just the answer. Why use many token when few token do trick. " + SAFETY_BOUNDARIES
    ),
    "ultra": (
        "Max brevity. Drop article, filler, pleasantry. Fragment fine, full sentence not "
        "required. No preamble, no commentary. Just answer. "
        "Why use many token when few token do trick. " + SAFETY_BOUNDARIES
    ),
}


class CavemanSaver:
    def __init__(self, level: str = "full") -> None:
        if level not in PROMPTS:
            raise ValueError(
                f"Invalid caveman level: {level}. Must be one of: {list(PROMPTS.keys())}"
            )
        self.level = level

    def transform(self, req: CanonicalRequest) -> CanonicalRequest:
        req.system.insert(0, SystemBlock(type="text", text=PROMPTS[self.level]))
        return req
