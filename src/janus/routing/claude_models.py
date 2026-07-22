"""Claude Code model discovery responses for GET /v1/models."""

from __future__ import annotations

from typing import Any

from janus.providers.registry import model_allowed as provider_model_allowed
from janus.storage.key_access import model_allowed as key_model_allowed


def _default_capabilities(*, reasoning: bool = True, tool_use: bool = True) -> dict[str, Any]:
    return {
        "audio_input": {"supported": False},
        "audio_output": {"supported": False},
        "batch": {"supported": False},
        "citations": {"supported": False},
        "code_execution": {"supported": False},
        "context_management": {
            "clear_thinking_20251015": {"supported": reasoning},
            "clear_tool_uses_20250919": {"supported": tool_use},
            "compact_20260112": {"supported": True},
            "max_input_tokens": 200000,
            "supported": True,
        },
        "context_window": {
            "max_input_tokens": 200000,
            "supported": True,
            "supports_1m_context": False,
            "one_million_context_variant": False,
        },
        "effort": {
            "high": {"supported": reasoning},
            "low": {"supported": reasoning},
            "max": {"supported": reasoning},
            "medium": {"supported": reasoning},
            "supported": reasoning,
            "ultra": {"supported": reasoning},
            "xhigh": {"supported": reasoning},
        },
        "image_input": {"supported": True},
        "pdf_input": {"supported": True},
        "structured_outputs": {"supported": True},
        "thinking": {
            "supported": reasoning,
            "types": {
                "adaptive": {"supported": reasoning},
                "enabled": {"supported": reasoning},
            },
        },
        "tool_use": {"supported": tool_use},
        "video_input": {"supported": False},
    }


def build_claude_code_models_response(
    *,
    registry_providers: dict[str, list[Any]],
    combos: dict[str, Any],
    allowed_models: list[str] | None,
) -> dict[str, Any]:
    """Anthropic-shaped model list for Claude Code capability discovery."""
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prefix, configs in registry_providers.items():
        for config in configs:
            for model in config.models:
                if model in seen:
                    continue
                if not provider_model_allowed(model, config.allowed_models):
                    continue
                model_id = f"{prefix}/{model}"
                if not key_model_allowed(model_id, allowed_models):
                    continue
                seen.add(model)
                bare_id = model.split("/")[-1] if "/" in model else model
                data.append(
                    {
                        "id": bare_id,
                        "display_name": bare_id,
                        "type": "model",
                        "created_at": "2025-01-01T00:00:00Z",
                        "capabilities": _default_capabilities(),
                    }
                )
    for combo_name in combos:
        if not key_model_allowed(combo_name, allowed_models):
            continue
        data.append(
            {
                "id": combo_name,
                "display_name": combo_name,
                "type": "model",
                "created_at": "2025-01-01T00:00:00Z",
                "capabilities": _default_capabilities(),
            }
        )
    return {"data": data, "has_more": False, "first_id": data[0]["id"] if data else None}
