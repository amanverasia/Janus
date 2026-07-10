"""Inject placeholder reasoning_content on assistant turns.

Ported from 9router ``open-sse/utils/reasoningContentInjector.js``.
Some thinking-mode providers (DeepSeek, Kimi, MiniMax, …) require
``reasoning_content`` to be echoed on assistant messages. OpenAI-format
clients omit it, so we inject a non-empty placeholder before dispatch.
"""

from __future__ import annotations

from typing import Any

_PLACEHOLDER = " "

# provider prefix → inject scope
_PROVIDER_RULES: dict[str, str] = {
    "deepseek": "all",
    "minimax": "all",
}

_MODEL_RULES: list[tuple[str, str]] = [
    ("kimi-", "toolCalls"),
    ("deepseek", "all"),
]

_DEEPSEEK_V4_PRO = "deepseek-v4-pro"
_DEEPSEEK_V4_PRO_ALIASES: dict[str, dict[str, Any]] = {
    f"{_DEEPSEEK_V4_PRO}-max": {
        "thinking_type": "enabled",
        "reasoning_effort": "max",
    },
    f"{_DEEPSEEK_V4_PRO}-none": {
        "thinking_type": "disabled",
        "reasoning_effort": None,
    },
}


def _match_rule(provider: str, model: str) -> str | None:
    if provider in _PROVIDER_RULES:
        return _PROVIDER_RULES[provider]
    m = (model or "").lower()
    for prefix, scope in _MODEL_RULES:
        if prefix in m:
            return scope
    return None


def _should_inject(message: dict[str, Any], scope: str) -> bool:
    if message.get("role") != "assistant":
        return False
    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        return False
    if scope == "toolCalls":
        return bool(message.get("tool_calls"))
    return True


def _apply_deepseek_v4_pro_alias(
    body: dict[str, Any],
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Map deepseek-v4-pro-max/none convenience ids onto real upstream fields."""
    if provider != "deepseek":
        return body
    alias = _DEEPSEEK_V4_PRO_ALIASES.get(model)
    if alias is None:
        return body
    body["model"] = _DEEPSEEK_V4_PRO
    thinking_type = alias["thinking_type"]
    body["thinking"] = {"type": thinking_type}
    extra = body.get("extra_body")
    if isinstance(extra, dict):
        raw_prev = extra.get("thinking")
        prev_dict: dict[str, Any] = raw_prev if isinstance(raw_prev, dict) else {}
        body["extra_body"] = {
            **extra,
            "thinking": {**prev_dict, "type": thinking_type},
        }
    effort = alias.get("reasoning_effort")
    if effort:
        body["reasoning_effort"] = effort
    else:
        body.pop("reasoning_effort", None)
    return body


def inject_reasoning_content(
    body: dict[str, Any],
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Mutate and return body with placeholder reasoning_content where needed."""
    body = _apply_deepseek_v4_pro_alias(body, provider=provider, model=model)
    scope = _match_rule(provider, model)
    messages = body.get("messages")
    if not scope or not isinstance(messages, list):
        return body
    new_messages: list[Any] = []
    changed = False
    for msg in messages:
        if isinstance(msg, dict) and _should_inject(msg, scope):
            new_messages.append({**msg, "reasoning_content": _PLACEHOLDER})
            changed = True
        else:
            new_messages.append(msg)
    if changed:
        body["messages"] = new_messages
    return body
