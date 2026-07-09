from __future__ import annotations

from typing import Any

from janus.canonical.models import CanonicalRequest, ImagePart, Role
from janus.routing.model_caps import get_model_capabilities

HARD_CAPS = frozenset({"vision", "pdf"})


def get_provider_capabilities(prefix: str) -> dict[str, Any]:
    from janus.catalog import PROVIDERS

    for entry in PROVIDERS.values():
        gateway = entry.get("gateway")
        if gateway is not None and gateway.get("prefix") == prefix:
            caps = entry.get("capabilities")
            if isinstance(caps, dict):
                return caps
            break
    return {"tool_use": True}


def get_capabilities_for_model(prefix: str, model: str) -> dict[str, Any]:
    """Provider-prefix defaults merged with per-model overrides."""
    return get_model_capabilities(prefix, model, provider_caps=get_provider_capabilities(prefix))


def detect_required_capabilities(req: CanonicalRequest) -> frozenset[str]:
    required: set[str] = set()
    for msg in reversed(req.messages):
        if msg.role != Role.USER:
            continue
        if isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, ImagePart):
                    required.add("vision")
        break
    for tool in req.tools:
        if "search" in tool.function.name.lower():
            required.add("search")
    return frozenset(required)


def reorder_combo_by_capabilities(models: list[str], required: frozenset[str]) -> list[str]:
    if not required or len(models) <= 1:
        return models
    hard = required & HARD_CAPS

    def tier(model_str: str) -> int:
        prefix = model_str.split("/", 1)[0] if "/" in model_str else model_str
        model = model_str.split("/", 1)[1] if "/" in model_str else model_str
        caps = get_capabilities_for_model(prefix, model)
        if not all(caps.get(c) for c in hard):
            return 2
        if all(caps.get(c) for c in required):
            return 0
        return 1

    return [m for _, m in sorted(enumerate(models), key=lambda im: (tier(im[1]), im[0]))]
