from __future__ import annotations


def provider_prefix_from_usage_id(provider_id: str | None) -> str:
    if not provider_id:
        return "unknown"
    if "::" in provider_id:
        return provider_id.split("::", 1)[0]
    return provider_id
