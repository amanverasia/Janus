from __future__ import annotations

import copy
from typing import Any

MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "model_id": "gpt-4o",
        "provider_id": "openai",
        "display_name": "GPT-4o",
        "context_window": 128_000,
        "max_output_tokens": 16_384,
        "pricing_input": 2.5,
        "pricing_output": 10.0,
        "capabilities": {"vision": True, "function_calling": True, "streaming": True},
        "status": "active",
    },
    {
        "model_id": "gpt-4o-mini",
        "provider_id": "openai",
        "display_name": "GPT-4o Mini",
        "context_window": 128_000,
        "max_output_tokens": 16_384,
        "pricing_input": 0.15,
        "pricing_output": 0.6,
        "capabilities": {"vision": True, "function_calling": True, "streaming": True},
        "status": "active",
    },
    {
        "model_id": "claude-sonnet-4-20250514",
        "provider_id": "anthropic",
        "display_name": "Claude Sonnet 4",
        "context_window": 200_000,
        "max_output_tokens": 64_000,
        "pricing_input": 3.0,
        "pricing_output": 15.0,
        "capabilities": {"vision": True, "function_calling": True, "streaming": True},
        "status": "active",
    },
    {
        "model_id": "llama-3.3-70b-versatile",
        "provider_id": "groq",
        "display_name": "Llama 3.3 70B Versatile",
        "context_window": 128_000,
        "tokens_per_second": 275,
        "capabilities": {"function_calling": True, "streaming": True},
        "status": "active",
    },
]

_CATALOG_INDEX: dict[tuple[str, str], dict[str, Any]] = {
    (entry["provider_id"], entry["model_id"]): entry for entry in MODEL_CATALOG
}


def enrich_model_with_catalog(model_id: str, provider_id: str) -> dict[str, Any] | None:
    entry = _CATALOG_INDEX.get((provider_id, model_id))
    if entry is None:
        return None
    return copy.deepcopy(entry)
