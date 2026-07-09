"""Unified thinking / reasoning normalization.

Ported from 9router ``open-sse/translator/concerns/thinking.js`` +
``thinkingUnified.js`` + ``providers/thinkingLevels.js``.

Flow:
1. Capture client intent from CanonicalRequest (or model suffix ``model(high)``).
2. Resolve per-provider thinking format from capabilities.
3. Apply provider-native fields onto the *upstream payload* after build.
"""

from __future__ import annotations

import re
from typing import Any

from janus.canonical.models import CanonicalRequest

LEVEL_TO_BUDGET: dict[str, int] = {
    "none": 0,
    "minimal": 512,
    "low": 1024,
    "medium": 8192,
    "high": 24576,
    "xhigh": 32768,
    "max": 128000,
}

EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")

FORMAT_LEVELS: dict[str, list[str]] = {
    "openai": ["none", "minimal", "low", "medium", "high", "xhigh"],
    "claude-adaptive": ["none", "low", "medium", "high", "max"],
    "claude-budget": ["none", "low", "medium", "high", "xhigh", "max"],
    "gemini-level": ["minimal", "low", "medium", "high"],
    "gemini-budget": ["none", "low", "medium", "high"],
    "zai": ["none", "thinking"],
    "qwen": ["none", "low", "medium", "high"],
    "kimi": ["none", "low", "medium", "high", "max"],
    "deepseek": ["none", "high", "max"],
    "minimax": ["none", "thinking"],
    "hunyuan": ["none", "low", "medium", "high"],
    "step": ["none", "low", "medium", "high"],
}

FORMAT_TO_NATIVE: dict[str, str] = {
    "openai": "openai",
    "openai_responses": "openai",
    "openai-responses": "openai",
    "anthropic": "claude-budget",
    "claude": "claude-budget",
    "gemini": "gemini-budget",
    "ollama": "openai",
    "kiro": "kiro",
    "codex": "openai",
    "antigravity": "gemini-budget",
    "cursor": "openai",
}

_SUFFIX_RE = re.compile(r"^(.*)\(([^()]+)\)\s*$")


def strip_thinking_suffix(model: str) -> str:
    if not isinstance(model, str):
        return model
    m = _SUFFIX_RE.match(model)
    return m.group(1).strip() if m else model


def parse_thinking_suffix(model: str) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(model, str):
        return model, None
    m = _SUFFIX_RE.match(model)
    if not m:
        return model, None
    clean = m.group(1).strip()
    raw = m.group(2).strip().lower()
    if raw in ("none", "off"):
        return clean, {"mode": "none"}
    if raw == "auto":
        return clean, {"mode": "auto"}
    if raw.isdigit():
        return clean, {"mode": "budget", "budget": int(raw)}
    if raw in LEVEL_TO_BUDGET:
        return clean, {"mode": "level", "level": raw}
    return clean, None


def extract_thinking(req: CanonicalRequest) -> dict[str, Any] | None:
    if req.thinking and isinstance(req.thinking, dict):
        t = req.thinking
        ttype = t.get("type")
        if ttype == "disabled":
            return {"mode": "none"}
        if ttype in ("adaptive", "enabled"):
            budget = t.get("budget_tokens")
            if isinstance(budget, (int, float)) and budget > 0:
                return {"mode": "budget", "budget": int(budget)}
            return {"mode": "auto"}
    effort = req.reasoning_effort
    if isinstance(effort, str) and effort:
        e = effort.lower()
        if e in ("none", "off"):
            return {"mode": "none"}
        if e == "auto":
            return {"mode": "auto"}
        return {"mode": "level", "level": e}
    return None


def effort_to_budget(effort: str | None) -> int | None:
    if not effort:
        return None
    return LEVEL_TO_BUDGET.get(str(effort).lower())


def budget_to_level(budget: int | float | None) -> str | None:
    if budget is None:
        return None
    b = float(budget)
    if b <= 0:
        return None
    if b <= 768:
        return "minimal"
    if b <= 4096:
        return "low"
    if b <= 16384:
        return "medium"
    if b <= 28672:
        return "high"
    return "xhigh"


def effort_to_thinking_level(effort: str) -> str:
    e = str(effort).lower().strip()
    if e in ("none", "off"):
        return "minimal"
    if e in ("xhigh", "max"):
        return "high"
    return e


def get_thinking_levels(caps: dict[str, Any]) -> list[str] | None:
    if not caps.get("reasoning"):
        return None
    fmt = caps.get("thinking_format") or caps.get("thinkingFormat")
    levels = list(FORMAT_LEVELS.get(fmt or "openai", FORMAT_LEVELS["openai"]))
    if caps.get("thinking_can_disable", caps.get("thinkingCanDisable", True)) is False:
        levels = [level for level in levels if level != "none"]
    return levels


def resolve_thinking_format(
    target_format: str,
    caps: dict[str, Any],
) -> str:
    fmt = caps.get("thinking_format") or caps.get("thinkingFormat")
    if isinstance(fmt, str) and fmt:
        return fmt
    return FORMAT_TO_NATIVE.get(target_format, "openai")


def _to_budget(cfg: dict[str, Any], caps: dict[str, Any]) -> int | None:
    budget: int | None
    if cfg["mode"] == "budget":
        budget = int(cfg["budget"])
    elif cfg["mode"] == "level":
        budget = effort_to_budget(cfg.get("level"))
    elif cfg["mode"] == "auto":
        return -1
    else:
        return None
    if budget is None:
        return None
    tr = caps.get("thinking_range") or caps.get("thinkingRange")
    if isinstance(tr, dict):
        mn = tr.get("min")
        mx = tr.get("max")
        if mn is not None and budget < mn:
            budget = int(mn)
        if mx is not None and budget > mx:
            budget = int(mx)
    return budget


def _to_level(cfg: dict[str, Any]) -> str | None:
    if cfg["mode"] == "level":
        return str(cfg.get("level"))
    if cfg["mode"] == "budget":
        return budget_to_level(cfg.get("budget")) or "medium"
    if cfg["mode"] == "auto":
        return "auto"
    return None


def _strip_thinking_fields(payload: dict[str, Any]) -> None:
    for key in (
        "thinking",
        "reasoning_effort",
        "reasoning",
        "thinkingConfig",
        "enable_thinking",
        "thinking_budget",
        "output_config",
    ):
        payload.pop(key, None)
    gc = payload.get("generationConfig")
    if isinstance(gc, dict):
        gc.pop("thinkingConfig", None)
    req = payload.get("request")
    if isinstance(req, dict):
        rgc = req.get("generationConfig")
        if isinstance(rgc, dict):
            rgc.pop("thinkingConfig", None)


def _set_gemini_thinking(payload: dict[str, Any], tc: dict[str, Any]) -> None:
    req = payload.get("request")
    if isinstance(req, dict) and isinstance(req.get("generationConfig"), dict):
        req["generationConfig"]["thinkingConfig"] = tc
        return
    gc = payload.get("generationConfig")
    if not isinstance(gc, dict):
        gc = {}
        payload["generationConfig"] = gc
    gc["thinkingConfig"] = tc


def _apply_format(
    fmt: str,
    payload: dict[str, Any],
    cfg: dict[str, Any],
    caps: dict[str, Any],
) -> None:
    none = cfg["mode"] == "none"
    can_disable = caps.get("thinking_can_disable", caps.get("thinkingCanDisable", True))
    if can_disable is None:
        can_disable = True
    eff = {"mode": "level", "level": "minimal"} if none and not can_disable else cfg

    if fmt == "openai":
        if none and can_disable:
            payload["reasoning_effort"] = "none"
            return
        level = _to_level(eff)
        if level:
            payload["reasoning_effort"] = level
        return

    if fmt == "claude-adaptive":
        if none and can_disable:
            payload["thinking"] = {"type": "disabled"}
            return
        level = _to_level(eff)
        if level == "xhigh":
            level = "high"
        payload["output_config"] = {"effort": level}
        return

    if fmt == "claude-budget":
        if none and can_disable:
            payload["thinking"] = {"type": "disabled"}
            return
        budget = _to_budget(eff, caps)
        if budget == -1:
            payload["thinking"] = {"type": "enabled"}
        else:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget or 8192,
            }
        return

    if fmt == "gemini-level":
        level = "minimal" if none else effort_to_thinking_level(_to_level(eff) or "high")
        _set_gemini_thinking(
            payload,
            {"thinkingLevel": level, "includeThoughts": level != "minimal"},
        )
        return

    if fmt == "gemini-budget":
        if none and can_disable:
            _set_gemini_thinking(
                payload, {"thinkingBudget": 0, "includeThoughts": False}
            )
            return
        budget = _to_budget(eff, caps)
        _set_gemini_thinking(
            payload,
            {"thinkingBudget": budget if budget is not None else -1, "includeThoughts": True},
        )
        return

    if fmt == "zai":
        if none and can_disable:
            payload["enable_thinking"] = False
            payload.pop("thinking", None)
            return
        payload["thinking"] = {"type": "enabled"}
        return

    if fmt == "qwen":
        if none and can_disable:
            payload["enable_thinking"] = False
            return
        payload["enable_thinking"] = True
        budget = _to_budget(eff, caps)
        if budget is not None and budget > 0:
            payload["thinking_budget"] = budget
        return

    if fmt == "deepseek":
        if none and can_disable:
            payload["thinking"] = {"type": "disabled"}
            return
        payload["thinking"] = {"type": "enabled"}
        level = _to_level(eff)
        payload["reasoning_effort"] = (
            "max" if level in ("xhigh", "max") else "high"
        )
        return

    if fmt == "kimi":
        if none and can_disable:
            payload["thinking"] = {"type": "disabled"}
            return
        level = _to_level(eff)
        if level == "auto":
            payload["reasoning_effort"] = "high"
        elif level == "minimal":
            payload["reasoning_effort"] = "low"
        elif level == "xhigh":
            payload["reasoning_effort"] = "max"
        elif level in ("low", "medium", "high", "max"):
            payload["reasoning_effort"] = level
        return

    if fmt == "minimax":
        payload["thinking"] = {
            "type": "disabled" if none and can_disable else "adaptive"
        }
        return

    if fmt in ("hunyuan",):
        if none and can_disable:
            payload["thinking"] = {"type": "disabled"}
            return
        budget = _to_budget(eff, caps)
        if budget == -1:
            payload["thinking"] = {"type": "enabled"}
        else:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget or 8192,
            }
        return

    if fmt == "step":
        if none and can_disable:
            return
        level = _to_level(eff)
        if level:
            payload["reasoning_effort"] = (
                "high" if level in ("xhigh", "max") else level
            )
        return


def apply_thinking_to_payload(
    payload: dict[str, Any],
    *,
    target_format: str,
    model: str,
    caps: dict[str, Any],
    intent: dict[str, Any] | None = None,
    provider_default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize thinking fields on an upstream request payload in-place."""
    clean_model, override = parse_thinking_suffix(model)
    cfg = override or intent or provider_default
    if not caps.get("reasoning", True) and not cfg:
        # Unknown models keep existing fields; only strip when explicitly non-reasoning.
        if caps.get("reasoning") is False:
            _strip_thinking_fields(payload)
        return payload
    if caps.get("reasoning") is False:
        _strip_thinking_fields(payload)
        return payload
    if not cfg:
        return payload
    fmt = resolve_thinking_format(target_format, caps)
    _strip_thinking_fields(payload)
    _apply_format(fmt, payload, cfg, caps)
    if "model" in payload and isinstance(payload["model"], str):
        payload["model"] = strip_thinking_suffix(payload["model"])
    return payload


def resolve_thinking_intent(
    req: CanonicalRequest,
    *,
    provider_default: dict[str, Any] | None = None,
) -> tuple[CanonicalRequest, dict[str, Any] | None]:
    """Return (req with cleaned model, thinking intent)."""
    clean_model, override = parse_thinking_suffix(req.model)
    intent = override or extract_thinking(req) or provider_default
    if clean_model != req.model:
        req = req.model_copy(update={"model": clean_model})
    return req, intent
