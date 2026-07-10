"""Fusion combo strategy: parallel panel fan-out + judge synthesis.

Port of 9router's ``handleFusionChat`` (open-sse/services/combo.js) adapted to
Janus's canonical request model. The combo's models form a *panel*: the request
is fanned out to all of them in parallel (non-streaming, tools stripped), then a
*judge* model synthesizes one authoritative answer from the anonymized panel
responses. The judge request is returned to the caller, which routes it through
the normal single-model attempt loop (streaming, fallback, usage recording).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException

from janus.canonical.models import (
    CanonicalRequest,
    Message,
    Role,
    TextPart,
    ToolResult,
    ToolUse,
    Usage,
    tool_result_text,
)
from janus.formats.base import FormatAdapter
from janus.pricing.calculator import compute_cost
from janus.pricing.registry import PricingRegistry
from janus.providers.base import Provider
from janus.routing.errors import is_fallback_eligible_refined, refine_error_type
from janus.routing.fallback import AllAccountsCooledDown, FallbackHandler
from janus.routing.model_aliases import resolve_model_alias
from janus.storage.usage import record_usage

logger = logging.getLogger(__name__)

TOOL_CALL_PREFIX = "[Called tools: "
TOOL_RESULT_PREFIX = "[Tool result: "
TOOL_RESULT_MAX_CHARS = 500


@dataclass
class PanelAnswer:
    """One successful panel response: the answering model and its prose."""

    model: str
    text: str
    usage: Usage


@dataclass
class FusionDeps:
    """Runtime dependencies threaded from routes into the panel calls."""

    handler: FallbackHandler
    providers: dict[str, Provider]
    resolve_format: Callable[[str], FormatAdapter]
    db_path: str | Path
    pricing_registry: PricingRegistry
    client_key_id: int | None
    client_key_label: str | None


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------


def _flatten_message(msg: Message) -> Message:
    if isinstance(msg.content, str):
        if msg.role is Role.TOOL:
            text = msg.content[:TOOL_RESULT_MAX_CHARS]
            return Message(role=Role.ASSISTANT, content=f"{TOOL_RESULT_PREFIX}{text}]")
        return msg
    if not any(isinstance(p, ToolUse | ToolResult) for p in msg.content):
        return msg
    texts = [p.text for p in msg.content if isinstance(p, TextPart) and p.text]
    names = [p.name or "tool" for p in msg.content if isinstance(p, ToolUse)]
    results = [
        tool_result_text(p.content)[:TOOL_RESULT_MAX_CHARS]
        for p in msg.content
        if isinstance(p, ToolResult)
    ]
    segments: list[str] = []
    if texts:
        segments.append("\n".join(texts))
    if names:
        segments.append(f"{TOOL_CALL_PREFIX}{', '.join(names)}]")
    if results:
        joined = "\n".join(results)
        segments.append(f"{TOOL_RESULT_PREFIX}{joined}]")
    role = Role.ASSISTANT if msg.role is Role.TOOL else msg.role
    return Message(role=role, content="\n".join(segments))


def flatten_tool_history(req: CanonicalRequest) -> CanonicalRequest:
    """Turn tool turns into assistant-visible prose so panel models keep context.

    Tool-result turns become assistant text ``[Tool result: …]`` (truncated to
    500 chars per result); assistant tool_use parts become ``[Called tools: …]``.
    Messages without tool parts pass through untouched.
    """
    return req.model_copy(update={"messages": [_flatten_message(m) for m in req.messages]})


def build_panel_request(req: CanonicalRequest) -> CanonicalRequest:
    """Panel calls want complete prose: strip tools, force non-streaming."""
    flat = flatten_tool_history(req)
    return flat.model_copy(update={"tools": [], "tool_choice": None, "stream": False})


def build_judge_prompt(answers: list[PanelAnswer]) -> str:
    """Build the judge directive with anonymized ``[Source N]`` panel answers.

    Per OpenRouter's Fusion design the judge does NOT merge — it analyzes
    (consensus / contradictions / partial coverage / unique insights / blind
    spots) then writes one answer grounded in that analysis. Sources are
    anonymized so the judge weighs substance, not model-brand reputation.
    """
    panel = "\n\n".join(f"[Source {i + 1}]\n{a.text}" for i, a in enumerate(answers))
    return "\n".join(
        [
            f"You are the JUDGE in a model-fusion panel. {len(answers)} expert models "
            "independently answered the user's most recent request. Their responses are "
            "below, anonymized by source.",
            "",
            "Do NOT mention that multiple models were used, and do NOT refer to the "
            "sources. Produce ONE authoritative final answer addressed directly to the "
            "user.",
            "",
            "First, internally analyze the panel along these dimensions: consensus "
            "(points most sources agree on — treat as higher-confidence), contradictions "
            "(where they disagree — resolve with your own judgment), partial coverage, "
            "unique insights only one source surfaced, and blind spots every source "
            "missed. Then write the best possible final answer grounded in that analysis "
            "— more complete and correct than any single response, with no filler.",
            "",
            "=== PANEL RESPONSES ===",
            panel,
            "=== END PANEL RESPONSES ===",
            "",
            "Now write the final answer to the user's original request.",
        ]
    )


def build_judge_request(req: CanonicalRequest, answers: list[PanelAnswer]) -> CanonicalRequest:
    """Original conversation + appended user turn with the synthesis directive.

    Keeps the client's original ``stream`` flag and tools so streaming and
    downstream tool use still work on the judge call.
    """
    judge_turn = Message(role=Role.USER, content=build_judge_prompt(answers))
    return req.model_copy(update={"messages": [*req.messages, judge_turn]})


# ---------------------------------------------------------------------------
# Panel execution
# ---------------------------------------------------------------------------


def _extract_text(resp_content: list[object]) -> str:
    return "".join(p.text for p in resp_content if isinstance(p, TextPart))


async def _call_panel_model(
    model: str,
    panel_req: CanonicalRequest,
    deps: FusionDeps,
) -> PanelAnswer | None:
    """One non-streaming attempt against the first available target for `model`.

    No intra-panel fallback (simplest viable); failures return None and the
    panel simply proceeds without this member.
    """
    handler = deps.handler
    try:
        attempts = handler.resolve_attempts(model)
    except (ValueError, AllAccountsCooledDown) as e:
        logger.warning("FUSION panel %s: no targets (%s)", model, e)
        return None
    target = attempts[0]
    provider = deps.providers.get(target.provider_config.id)
    if provider is None:
        logger.warning("FUSION panel %s: provider %s missing", model, target.provider_config.id)
        return None

    upstream_model, _ = resolve_model_alias(target.prefix, target.model)
    adapter = deps.resolve_format(target.native_format)
    payload = adapter.build_upstream_request(panel_req, upstream_model)
    handler.record_attempt(target)
    try:
        result = await provider.call(payload, stream=False)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        handler.mark_cooldown(target.account_id, "network", model=target.model)
        logger.warning("FUSION panel %s: %s", model, type(e).__name__)
        return None
    if result.status_code >= 400:
        if is_fallback_eligible_refined(result.status_code, result.json_data):
            handler.mark_cooldown(
                target.account_id,
                refine_error_type(result.status_code, result.json_data).value,
                model=target.model,
                retry_after=result.retry_after,
            )
        logger.warning("FUSION panel %s: upstream %s", model, result.status_code)
        return None
    if result.json_data is None:
        logger.warning("FUSION panel %s: empty upstream response", model)
        return None
    try:
        canonical_resp = adapter.parse_upstream_response(result.json_data)
    except Exception:
        logger.warning("FUSION panel %s: unparseable response", model)
        return None
    text = _extract_text(list(canonical_resp.content))
    if not text.strip():
        logger.warning("FUSION panel %s: empty content", model)
        return None

    usage = canonical_resp.usage
    handler.record_quota_tokens(target, usage.input_tokens + usage.output_tokens)
    await record_usage(
        deps.db_path,
        provider_id=target.provider_config.id,
        model=target.model,
        account_id=target.account_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        status=200,
        client_key_id=deps.client_key_id,
        client_key_label=deps.client_key_label,
        cost=compute_cost(usage, target.model, deps.pricing_registry),
    )
    handler.mark_success(target.account_id, target.model)
    logger.info("FUSION panel %s ok (%d chars)", model, len(text))
    return PanelAnswer(model=model, text=text, usage=usage)


async def collect_panel(
    tasks: list[asyncio.Task[PanelAnswer | None]],
    *,
    min_panel: int,
    straggler_grace_s: float,
    hard_timeout_s: float,
) -> list[PanelAnswer]:
    """Quorum-grace collection: once `min_panel` answers arrive, wait a short
    grace window for stragglers, then cancel the rest. Bounded by a hard cap so
    one hung model can't stall forever. Returns answers in panel order.
    """
    deadline = time.monotonic() + hard_timeout_s
    grace_deadline: float | None = None
    ok = 0
    pending: set[asyncio.Task[PanelAnswer | None]] = set(tasks)
    try:
        while pending:
            now = time.monotonic()
            timeout = deadline - now
            if grace_deadline is not None:
                timeout = min(timeout, grace_deadline - now)
            if timeout <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    answer = task.result()
                except Exception:
                    continue
                if answer is not None and answer.text:
                    ok += 1
            if ok >= min_panel and grace_deadline is None:
                grace_deadline = time.monotonic() + straggler_grace_s
    finally:
        # Runs on the happy path AND on cancellation (e.g. client disconnect):
        # never leave panel tasks running unattended, burning tokens until
        # their individual hard_timeout_s wait_for caps.
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    answers: list[PanelAnswer] = []
    for task in tasks:
        if not task.done() or task.cancelled():
            continue
        try:
            answer = task.result()
        except Exception:
            continue
        if answer is not None and answer.text:
            answers.append(answer)
    return answers


async def run_fusion(
    canonical_req: CanonicalRequest,
    panel_models: list[str],
    *,
    judge_model: str,
    deps: FusionDeps,
    min_panel: int,
    straggler_grace_s: float,
    hard_timeout_s: float,
) -> CanonicalRequest:
    """Fan the request out to the panel, then return the judge-ready request.

    The caller rewrites its canonical request with the returned one (its
    ``.model`` is the judge model) and continues the normal attempt loop.

    Degrades gracefully: 0 panel answers → HTTPException 503; exactly 1 →
    the original request pinned to the sole answering model (no judge turn).
    """
    quorum = min(max(2, min_panel), len(panel_models))
    logger.info(
        "FUSION panel=%d %s | judge=%s | quorum=%d",
        len(panel_models),
        panel_models,
        judge_model,
        quorum,
    )
    panel_req = build_panel_request(canonical_req)
    tasks = [
        asyncio.create_task(asyncio.wait_for(_call_panel_model(m, panel_req, deps), hard_timeout_s))
        for m in panel_models
    ]
    answers = await collect_panel(
        tasks,
        min_panel=quorum,
        straggler_grace_s=straggler_grace_s,
        hard_timeout_s=hard_timeout_s,
    )
    if not answers:
        raise HTTPException(status_code=503, detail="All fusion panel models failed")
    if len(answers) == 1:
        logger.info("FUSION only %s succeeded — answering directly (no fusion)", answers[0].model)
        return canonical_req.model_copy(update={"model": answers[0].model})
    logger.info("FUSION judging %d answers with %s", len(answers), judge_model)
    judge_req = build_judge_request(canonical_req, answers)
    return judge_req.model_copy(update={"model": judge_model})
