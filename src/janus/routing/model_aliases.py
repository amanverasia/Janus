"""Client-facing model id aliases → real upstream model ids.

Ported from 9router ``getModelUpstreamId`` + DeepSeek V4 Pro alias handling
in ``reasoningContentInjector.js``.

Example: ``deepseek-v4-pro-max`` is a Janus/9router convenience id that maps
to upstream ``deepseek-v4-pro`` with thinking forced to max effort.
"""

from __future__ import annotations

from typing import Any

# model_id → (upstream_model_id, optional thinking intent override)
_MODEL_ALIASES: dict[str, tuple[str, dict[str, Any] | None]] = {
    "deepseek-v4-pro-max": ("deepseek-v4-pro", {"mode": "level", "level": "max"}),
    "deepseek-v4-pro-none": ("deepseek-v4-pro", {"mode": "none"}),
}

# provider-scoped aliases (only apply when prefix matches)
_PROVIDER_ALIASES: dict[str, dict[str, tuple[str, dict[str, Any] | None]]] = {
    "deepseek": {
        "deepseek-v4-pro-max": ("deepseek-v4-pro", {"mode": "level", "level": "max"}),
        "deepseek-v4-pro-none": ("deepseek-v4-pro", {"mode": "none"}),
    },
    "xiaomi": {
        # Convenience Claude-compat id → real paygo model.
        "mimo-v2.5-pro-claude": ("mimo-v2.5-pro", None),
    },
    "xmtp": {
        "mimo-v2.5-pro-claude": ("mimo-v2.5-pro", None),
    },
}


def resolve_model_alias(
    provider: str,
    model: str,
) -> tuple[str, dict[str, Any] | None]:
    """Return ``(upstream_model, thinking_intent_or_None)``.

    Thinking intent is only returned for alias-driven defaults (e.g. ``-max`` /
    ``-none``). Callers should prefer an explicit client thinking intent when
    both are present.
    """
    if not model:
        return model, None
    scoped = _PROVIDER_ALIASES.get(provider or "", {}).get(model)
    if scoped is not None:
        return scoped
    global_alias = _MODEL_ALIASES.get(model)
    if global_alias is not None:
        return global_alias
    return model, None
