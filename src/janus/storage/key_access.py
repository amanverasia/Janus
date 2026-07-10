from __future__ import annotations

import json
from typing import Any


def parse_allowed_models(raw: str | list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        patterns = [p.strip() for p in raw if isinstance(p, str) and p.strip()]
        return patterns or None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        patterns = [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]
        return patterns or None
    if not isinstance(parsed, list):
        return None
    patterns = [p.strip() for p in parsed if isinstance(p, str) and p.strip()]
    return patterns or None


def serialize_allowed_models(allowed: list[str] | None) -> str | None:
    if not allowed:
        return None
    return json.dumps(list(allowed))


def model_allowed(model: str, allowed: list[str] | None) -> bool:
    if allowed is None:
        return True
    for pattern in allowed:
        if pattern == model:
            return True
        if pattern.endswith("/*"):
            prefix = pattern[:-1]
            if model.startswith(prefix):
                return True
    return False


def parse_models_input(text: str) -> list[str] | None:
    patterns = [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]
    return patterns or None
