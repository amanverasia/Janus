"""Anthropic beta header helpers for Claude Code upstream requests."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

DEFAULT_CLAUDE_BETAS = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
    "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
    "advanced-tool-use-2025-11-20,effort-2025-11-24,structured-outputs-2025-12-15,"
    "fast-mode-2026-02-01,redact-thinking-2026-02-12,token-efficient-tools-2026-03-28"
)


def merge_anthropic_beta_values(*values: str | None) -> str:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if not value:
            continue
        for token in value.split(","):
            normalized = token.strip()
            key = normalized.lower()
            if not normalized or key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return ",".join(merged)


def _incoming_beta(headers: dict[str, str] | None) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if key.lower() == "anthropic-beta" and value.strip():
            return value.strip()
    return ""


def build_claude_upstream_headers(
    *,
    incoming_headers: dict[str, str] | None = None,
    extra_betas: list[str] | None = None,
    oauth: bool = False,
    stream: bool = False,
    session_seed: str = "",
    api_key_auth: bool = False,
) -> dict[str, str]:
    """Headers that make Claude Code / OAuth upstream requests acceptable to Anthropic."""
    client_beta = _incoming_beta(incoming_headers)
    base = client_beta or DEFAULT_CLAUDE_BETAS
    if oauth and "oauth" not in base.lower():
        base = merge_anthropic_beta_values(base, "oauth-2025-04-20")
    if "interleaved-thinking" not in base.lower():
        base = merge_anthropic_beta_values(base, "interleaved-thinking-2025-05-14")
    if extra_betas:
        base = merge_anthropic_beta_values(base, ",".join(extra_betas))

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Anthropic-Version": "2023-06-01",
        "Anthropic-Beta": base,
        "X-App": "cli",
        "X-Stainless-Retry-Count": "0",
        "X-Stainless-Runtime": "node",
        "X-Stainless-Lang": "js",
        "X-Stainless-Timeout": "600",
        "x-client-request-id": str(uuid.uuid4()),
    }
    if oauth:
        headers["User-Agent"] = "claude-cli/2.1.92 (external, sdk-cli)"
    if api_key_auth:
        headers["Anthropic-Dangerous-Direct-Browser-Access"] = "true"
    if session_seed:
        digest = hashlib.sha256(session_seed.encode()).hexdigest()
        headers["X-Claude-Code-Session-Id"] = digest[:32]
    if stream:
        headers["Accept"] = "text/event-stream"
        headers["Accept-Encoding"] = "identity"
    return headers


def apply_claude_upstream_headers(
    base: dict[str, str],
    *,
    incoming_headers: dict[str, str] | None = None,
    extra_betas: list[str] | None = None,
    oauth: bool = False,
    stream: bool = False,
    session_seed: str = "",
    api_key_auth: bool = False,
) -> dict[str, str]:
    """Merge Claude Code header defaults into an existing provider header dict."""
    built = build_claude_upstream_headers(
        incoming_headers=incoming_headers,
        extra_betas=extra_betas,
        oauth=oauth,
        stream=stream,
        session_seed=session_seed,
        api_key_auth=api_key_auth,
    )
    merged = dict(base)
    for key, value in built.items():
        existing = merged.get(key) or merged.get(key.lower())
        if key.lower() == "anthropic-beta" and existing:
            merged[key] = merge_anthropic_beta_values(str(existing), value)
        elif key not in merged and key.lower() not in {k.lower() for k in merged}:
            merged[key] = value
    if stream:
        merged["Accept"] = "text/event-stream"
        merged["Accept-Encoding"] = "identity"
    return merged


def client_header_map(request_headers: Any) -> dict[str, str]:
    if request_headers is None:
        return {}
    return {str(k): str(v) for k, v in request_headers.items()}
