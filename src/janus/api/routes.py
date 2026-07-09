from __future__ import annotations

import datetime
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, NoReturn

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from janus.api.auth import key_allowed_models
from janus.canonical.models import Usage
from janus.canonical.tool_calls import prepare_tool_messages
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.base import FormatAdapter
from janus.formats.gemini import GeminiAdapter
from janus.formats.ollama import OllamaAdapter
from janus.formats.openai import OpenAIAdapter
from janus.formats.openai_responses import OpenAIResponsesAdapter
from janus.pricing.calculator import compute_cost
from janus.providers.base import (
    Provider,
    RawResult,
    parse_error_body,
    parse_retry_after,
)
from janus.providers.registry import ProviderRegistry, ResolvedTarget
from janus.routing.capabilities import (
    detect_required_capabilities,
    get_capabilities_for_model,
)
from janus.routing.claude_normalize import normalize_claude_passthrough
from janus.routing.client_detect import detect_client_tool, is_native_passthrough
from janus.routing.errors import classify_error, is_fallback_eligible
from janus.routing.fallback import AccountStrategy, FallbackHandler
from janus.routing.modality import strip_unsupported_modalities
from janus.routing.model_aliases import resolve_model_alias
from janus.routing.prefetch import prefetch_remote_images
from janus.routing.reasoning_inject import inject_reasoning_content
from janus.routing.thinking import (
    apply_thinking_to_payload,
    resolve_thinking_intent,
)
from janus.routing.tool_dedupe import dedupe_tools
from janus.storage.budgets import get_budget_status
from janus.storage.key_access import model_allowed
from janus.storage.request_logs import record_request_log
from janus.storage.usage import record_usage
from janus.streaming.passthrough import generic_sse_passthrough, openai_passthrough_stream
from janus.streaming.translator import translate_stream
from janus.streaming.usage import StreamUsageTracker
from janus.tokensavers.pipeline import SaverPipeline

from .deps import require_api_key

logger = logging.getLogger(__name__)

router = APIRouter()

FORMATS: dict[str, FormatAdapter] = {
    "openai": OpenAIAdapter(),
    "openai_responses": OpenAIResponsesAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
    "ollama": OllamaAdapter(),
}


def _resolve_format(name: str) -> FormatAdapter:
    if name in (
        "opencode_free",
        "github_copilot",
        "cursor",
        "kiro",
        "codex",
    ):
        # Specialized executors that still speak OpenAI-shaped JSON payloads
        # (Codex uses Responses; map when native_format is codex via registry).
        if name == "codex":
            name = "openai_responses"
        else:
            name = "openai"
    if name in ("antigravity", "gemini_cli", "gemini-cli"):
        name = "gemini"
    return FORMATS[name]


async def _check_budgets(
    db_path: str | Path,
    client_key_id: int | None,
) -> Response | None:
    try:
        statuses: list[dict[str, Any]] = []
        key_status = await get_budget_status(db_path, key_id=client_key_id)
        if key_status is not None:
            statuses.append(key_status)
        global_status = await get_budget_status(db_path, key_id=None)
        if global_status is not None:
            statuses.append(global_status)
        for s in statuses:
            if s["status"] == "exceeded":
                now = datetime.datetime.now()
                midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
                retry_after = int((midnight - now).total_seconds()) + 1
                error_body: dict[str, Any] = {
                    "error": {
                        "message": (
                            f"Daily budget exceeded. "
                            f"Spent ${s['today_spend']:.2f} of ${s['daily_limit']:.2f} limit. "
                            f"Resets at midnight."
                        ),
                        "type": "budget_exceeded",
                        "today_spend": round(s["today_spend"], 4),
                        "daily_limit": s["daily_limit"],
                    }
                }
                return JSONResponse(
                    content=error_body,
                    status_code=429,
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
    except Exception as e:
        logger.warning("Budget check failed, allowing request: %s", e, exc_info=True)
    return None


def _passthrough_url(base_url: str, fmt: str) -> str:
    base = base_url.rstrip("/")
    if fmt == "anthropic":
        return f"{base}/messages"
    return f"{base}/chat/completions"


def _passthrough_headers(
    provider: Provider,
    *,
    fmt: str,
    stream: bool,
) -> dict[str, str]:
    raw_headers = getattr(provider, "_headers", None)
    if raw_headers is None:
        headers: dict[str, str] = {}
    elif callable(raw_headers):
        headers = dict(raw_headers())
    else:
        headers = dict(raw_headers)
    headers.setdefault("Content-Type", "application/json")
    if stream:
        headers["Accept"] = "text/event-stream"
    if fmt == "anthropic":
        api_key = getattr(provider, "api_key", None)
        if not api_key:
            auth = headers.get("Authorization") or headers.get("authorization")
            if isinstance(auth, str) and auth.lower().startswith("bearer "):
                api_key = auth.split(" ", 1)[1].strip() or None
        if api_key:
            headers.setdefault("x-api-key", str(api_key))
        headers.setdefault("anthropic-version", "2023-06-01")
        headers.setdefault(
            "anthropic-beta",
            (
                "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
                "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
                "advanced-tool-use-2025-11-20,effort-2025-11-24,"
                "structured-outputs-2025-12-15,fast-mode-2026-02-01,"
                "redact-thinking-2026-02-12,token-efficient-tools-2026-03-28"
            ),
        )
    return headers


async def _passthrough_call(
    base_url: str,
    fmt: str,
    body: dict[str, Any],
    stream: bool,
    request: Request,
    target: ResolvedTarget,
) -> RawResult | None:
    providers: dict[str, Provider] = request.app.state.providers
    provider = providers.get(target.provider_config.id)
    if provider is None:
        return None
    handler: FallbackHandler = request.app.state.fallback_handler
    handler.record_attempt(target)
    url = _passthrough_url(base_url, fmt)
    client = getattr(provider, "_client", None)
    if client is None:
        return None
    headers = _passthrough_headers(provider, fmt=fmt, stream=stream)
    if stream:
        cm = client.stream("POST", url, json=body, headers=headers)
        r = await cm.__aenter__()
        if r.status_code >= 400:
            err_body = await r.aread()
            await cm.__aexit__(None, None, None)
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(err_body),
                retry_after=parse_retry_after(r.headers),
            )

        async def line_iter() -> AsyncIterator[str]:
            try:
                async for raw in r.aiter_lines():
                    yield raw
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())
    r = await client.post(url, json=body, headers=headers)
    try:
        json_data = r.json()
    except Exception:
        json_data = {"error": r.text[:500]}
    return RawResult(
        status_code=r.status_code,
        json_data=json_data,
        retry_after=parse_retry_after(r.headers) if r.status_code >= 400 else None,
    )


async def _log_error_and_raise(
    *,
    log_requests: bool,
    db_path: str | Path,
    client_format: str,
    model: str | None,
    provider_id: str | None,
    account_id: str | None,
    status: int,
    duration_ms: int,
    request_body: str | None,
    detail: Any,
    response_body: Any = None,
) -> NoReturn:
    """Record a non-fallback upstream error then raise HTTPException."""
    if log_requests:
        err_text = detail if isinstance(detail, str) else str(detail)
        resp_text: str | None = None
        if response_body is not None:
            try:
                resp_text = (
                    response_body
                    if isinstance(response_body, str)
                    else json.dumps(response_body, ensure_ascii=False)
                )
            except (TypeError, ValueError):
                resp_text = str(response_body)
        await record_request_log(
            db_path,
            client_format=client_format,
            model=model,
            provider_id=provider_id,
            account_id=account_id,
            status=status,
            duration_ms=duration_ms,
            request_body=request_body,
            response_body=resp_text,
            error=err_text[:2000],
        )
    raise HTTPException(status_code=status, detail=detail)


def _apply_client_body_quirks(
    body: dict[str, Any],
    *,
    client_format: str,
    client_tool: str | None,
    model: str,
    provider_prefix: str,
) -> dict[str, Any]:
    """Claude normalize + tool dedupe on final wire bodies."""
    if client_format == "anthropic" or client_tool == "claude":
        body = normalize_claude_passthrough(body, model)
        tools = body.get("tools")
        if isinstance(tools, list):
            deduped, stripped = dedupe_tools(tools)
            if stripped:
                body["tools"] = deduped
                logger.debug("Deduped tools for %s: %s", provider_prefix, stripped[:5])
    # Mistral (and some OpenAI-compat gateways) reject Anthropic client_metadata.
    if provider_prefix in {"mistral", "mistralai"} and "client_metadata" in body:
        body = {k: v for k, v in body.items() if k != "client_metadata"}
    return body


async def _handle(
    client_format: str,
    body: dict[str, Any],
    request: Request,
) -> Response:
    handler: FallbackHandler = request.app.state.fallback_handler
    db_path = request.app.state.db_path
    pricing_registry = request.app.state.pricing_registry

    client_key_id = getattr(request.state, "client_key_id", None)
    client_key_label = getattr(request.state, "client_key_label", None)

    blocked_response = await _check_budgets(db_path, client_key_id)
    if blocked_response is not None:
        return blocked_response

    client_adapter = FORMATS[client_format]
    canonical_req = client_adapter.parse_request(body)

    # Client detection (Claude Code / Codex / Gemini CLI / Copilot / …)
    header_map = {k.lower(): v for k, v in request.headers.items()}
    client_tool = detect_client_tool(header_map, body)
    if client_tool:
        logger.debug("Detected client tool: %s", client_tool)
    # DeepSeek-TUI non-interactive mode can't parse SSE unless stream was explicit
    if client_tool == "deepseek-tui" and body.get("stream") is not True:
        body = {**body, "stream": False}
        canonical_req = client_adapter.parse_request(body)

    # Strip model thinking suffix like "gpt-4o(high)" → clean model + intent
    canonical_req, thinking_intent = resolve_thinking_intent(canonical_req)

    allowed = key_allowed_models(request)
    if not model_allowed(canonical_req.model, allowed):
        return JSONResponse(
            content={
                "error": {
                    "message": f"Model '{canonical_req.model}' is not allowed for this API key",
                    "type": "model_not_allowed",
                    "model": canonical_req.model,
                }
            },
            status_code=403,
        )

    saver_pipeline: SaverPipeline = request.app.state.saver_pipeline
    canonical_req = await saver_pipeline.apply_async(canonical_req)
    canonical_req = saver_pipeline.apply(canonical_req)
    canonical_req = canonical_req.model_copy(
        update={"messages": prepare_tool_messages(canonical_req.messages)},
    )

    from janus.storage.settings import (
        get_all_settings,
        request_logging_enabled,
        resolve_account_strategy,
        resolve_combo_sticky_limit,
        resolve_combo_strategy,
        resolve_sticky_limit,
        sticky_client_key_routing_enabled,
    )

    settings = await get_all_settings(db_path)
    sticky_routing = sticky_client_key_routing_enabled(settings)
    log_requests = request_logging_enabled(settings)
    try:
        strategy = AccountStrategy(resolve_account_strategy(settings))
    except ValueError:
        strategy = AccountStrategy.ROUND_ROBIN
    sticky_limit = resolve_sticky_limit(settings)
    combo_strat = resolve_combo_strategy(settings)
    combo_csl = resolve_combo_sticky_limit(settings)
    start_time = time.monotonic()
    logged_request_body: str | None = None
    if log_requests:
        try:
            logged_request_body = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            logged_request_body = str(body)

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start_time) * 1000)

    required_caps = detect_required_capabilities(canonical_req)
    try:
        attempts = handler.resolve_attempts(
            canonical_req.model,
            client_key_id=client_key_id,
            sticky_client_key=sticky_routing,
            strategy=strategy,
            sticky_limit=sticky_limit,
            required_caps=required_caps,
            combo_strategy=combo_strat,
            combo_sticky_limit=combo_csl,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    last_error = "Unknown error"
    for target in attempts:
        if not handler.is_available(target.account_id, target.model):
            continue

        upstream_model, alias_intent = resolve_model_alias(target.prefix, target.model)
        attempt_thinking = thinking_intent or alias_intent
        model_caps = get_capabilities_for_model(target.prefix, target.model)
        attempt_req = strip_unsupported_modalities(canonical_req, model_caps)
        if target.native_format in ("gemini", "ollama", "antigravity", "kiro", "vertex"):
            attempt_req = await prefetch_remote_images(attempt_req, target.native_format)

        # ── Multi-endpoint transport passthrough ──────────────────────
        # Same-format alternate base URL (e.g. DeepSeek Anthropic endpoint).
        # Rebuild from post-saver CanonicalRequest so RTK/Caveman still apply.
        transports = target.provider_config.transports or {}
        transport_base = transports.get(client_format, "")
        if transport_base:
            providers_t: dict[str, Provider] = request.app.state.providers
            if target.provider_config.id in providers_t:
                pt_body = client_adapter.build_upstream_request(attempt_req, upstream_model)
                apply_thinking_to_payload(
                    pt_body,
                    target_format=client_format,
                    model=upstream_model,
                    caps=model_caps,
                    intent=attempt_thinking,
                )
                inject_reasoning_content(pt_body, provider=target.prefix, model=target.model)
                pt_body = _apply_client_body_quirks(
                    pt_body,
                    client_format=client_format,
                    client_tool=client_tool,
                    model=upstream_model,
                    provider_prefix=target.prefix,
                )
                pt_stream = attempt_req.stream
                media_type = getattr(client_adapter, "stream_media_type", "text/event-stream")
                try:
                    result = await _passthrough_call(
                        transport_base, client_format, pt_body, pt_stream, request, target
                    )
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    handler.mark_cooldown(target.account_id, "network", model=target.model)
                    last_error = f"{target.account_id}: {type(e).__name__}"
                    continue
                if result is None:
                    continue
                if result.status_code >= 400:
                    if is_fallback_eligible(result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(result.status_code).value,
                            model=target.model,
                            retry_after=getattr(result, "retry_after", None),
                        )
                        last_error = f"{target.account_id}: {result.status_code}"
                        continue
                    await _log_error_and_raise(
                        log_requests=log_requests,
                        db_path=db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=result.status_code,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        detail="Upstream error",
                        response_body=result.json_data,
                    )
                if pt_stream:
                    lines = result.lines
                    if lines is None:
                        raise HTTPException(status_code=502, detail="No stream from upstream")
                    parser = client_adapter.stream_parser()
                    tracker = StreamUsageTracker(parser)
                    _live_lines = lines
                    _pt_model = target.model
                    _pt_provider = target.prefix
                    _pt_is_openai = client_format == "openai"

                    async def _pt_stream() -> AsyncIterator[bytes]:
                        stream_ok = False
                        try:
                            if _pt_is_openai:
                                async for chunk in openai_passthrough_stream(
                                    _live_lines,
                                    tracker=tracker,
                                    model=_pt_model,
                                    provider=_pt_provider,
                                ):
                                    yield chunk
                            else:
                                async for chunk in generic_sse_passthrough(
                                    _live_lines, tracker=tracker
                                ):
                                    yield chunk
                            stream_ok = True
                        finally:
                            usage = tracker.get_usage()
                            cost = compute_cost(usage, target.model, pricing_registry)
                            handler.record_quota_tokens(
                                target, usage.input_tokens + usage.output_tokens
                            )
                            await record_usage(
                                db_path,
                                provider_id=target.provider_config.id,
                                model=target.model,
                                account_id=target.account_id,
                                input_tokens=usage.input_tokens,
                                output_tokens=usage.output_tokens,
                                cache_creation_tokens=usage.cache_creation_input_tokens,
                                cache_read_tokens=usage.cache_read_input_tokens,
                                status=200,
                                client_key_id=client_key_id,
                                client_key_label=client_key_label,
                                cost=cost,
                            )
                            if log_requests:
                                await record_request_log(
                                    db_path,
                                    client_format=client_format,
                                    model=canonical_req.model,
                                    provider_id=target.provider_config.id,
                                    account_id=target.account_id,
                                    status=200,
                                    duration_ms=_elapsed_ms(),
                                    streamed=True,
                                    request_body=logged_request_body,
                                )
                            if stream_ok:
                                handler.mark_success(target.account_id, target.model)

                    return StreamingResponse(_pt_stream(), media_type=media_type)

                pt_usage = Usage(input_tokens=0, output_tokens=0)
                if result.json_data:
                    try:
                        pt_usage = client_adapter.parse_upstream_response(result.json_data).usage
                    except Exception:
                        pass
                await record_usage(
                    db_path,
                    provider_id=target.provider_config.id,
                    model=target.model,
                    account_id=target.account_id,
                    input_tokens=pt_usage.input_tokens,
                    output_tokens=pt_usage.output_tokens,
                    cache_creation_tokens=pt_usage.cache_creation_input_tokens,
                    cache_read_tokens=pt_usage.cache_read_input_tokens,
                    status=200,
                    client_key_id=client_key_id,
                    client_key_label=client_key_label,
                    cost=compute_cost(pt_usage, target.model, pricing_registry),
                )
                handler.record_quota_tokens(target, pt_usage.input_tokens + pt_usage.output_tokens)
                if log_requests:
                    try:
                        pt_response_body: str | None = json.dumps(
                            result.json_data, ensure_ascii=False
                        )
                    except (TypeError, ValueError):
                        pt_response_body = str(result.json_data)
                    await record_request_log(
                        db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=200,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        response_body=pt_response_body,
                    )
                handler.mark_success(target.account_id, target.model)
                return JSONResponse(content=result.json_data if result.json_data else {})
        # ── End transport passthrough ───────────────────────────────────

        # ── Native-format passthrough ──────────────────────────────────
        # Same wire format on both sides: build from the *post-saver*
        # CanonicalRequest (so RTK/Caveman/Ponytail still apply), then either
        # stream with full usage/log lifecycle or return JSON with usage.
        if client_format == target.native_format:
            providers_p: dict[str, Provider] = request.app.state.providers
            provider_p = providers_p.get(target.provider_config.id)
            if provider_p is not None:
                handler.record_attempt(target)
                native_body = client_adapter.build_upstream_request(attempt_req, upstream_model)
                apply_thinking_to_payload(
                    native_body,
                    target_format=target.native_format,
                    model=upstream_model,
                    caps=model_caps,
                    intent=attempt_thinking,
                )
                inject_reasoning_content(native_body, provider=target.prefix, model=target.model)
                native_body = _apply_client_body_quirks(
                    native_body,
                    client_format=client_format,
                    client_tool=client_tool,
                    model=upstream_model,
                    provider_prefix=target.prefix,
                )
                if is_native_passthrough(client_tool, target.prefix):
                    logger.debug(
                        "Client-native pair: %s → %s",
                        client_tool,
                        target.prefix,
                    )
                native_stream = attempt_req.stream
                native_media = getattr(client_adapter, "stream_media_type", "text/event-stream")
                try:
                    native_result = await provider_p.call(native_body, stream=native_stream)
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    handler.mark_cooldown(target.account_id, "network", model=target.model)
                    last_error = f"{target.account_id}: {type(e).__name__}"
                    continue
                if native_result.status_code >= 400:
                    if is_fallback_eligible(native_result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(native_result.status_code).value,
                            model=target.model,
                            retry_after=getattr(native_result, "retry_after", None),
                        )
                        last_error = f"{target.account_id}: {native_result.status_code}"
                        continue
                    await _log_error_and_raise(
                        log_requests=log_requests,
                        db_path=db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=native_result.status_code,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        detail="Upstream error",
                        response_body=native_result.json_data,
                    )

                if native_stream:
                    native_lines = native_result.lines
                    if native_lines is None:
                        raise HTTPException(status_code=502, detail="No stream from upstream")
                    parser = client_adapter.stream_parser()
                    tracker = StreamUsageTracker(parser)
                    _native_model = target.model
                    _native_provider = target.prefix
                    _native_is_openai = client_format == "openai"

                    async def _native_stream() -> AsyncIterator[bytes]:
                        stream_ok = False
                        try:
                            if _native_is_openai:
                                async for chunk in openai_passthrough_stream(
                                    native_lines,
                                    tracker=tracker,
                                    model=_native_model,
                                    provider=_native_provider,
                                ):
                                    yield chunk
                            else:
                                async for chunk in generic_sse_passthrough(
                                    native_lines, tracker=tracker
                                ):
                                    yield chunk
                            stream_ok = True
                        finally:
                            usage = tracker.get_usage()
                            cost = compute_cost(usage, target.model, pricing_registry)
                            handler.record_quota_tokens(
                                target, usage.input_tokens + usage.output_tokens
                            )
                            await record_usage(
                                db_path,
                                provider_id=target.provider_config.id,
                                model=target.model,
                                account_id=target.account_id,
                                input_tokens=usage.input_tokens,
                                output_tokens=usage.output_tokens,
                                cache_creation_tokens=usage.cache_creation_input_tokens,
                                cache_read_tokens=usage.cache_read_input_tokens,
                                status=200,
                                client_key_id=client_key_id,
                                client_key_label=client_key_label,
                                cost=cost,
                            )
                            if log_requests:
                                await record_request_log(
                                    db_path,
                                    client_format=client_format,
                                    model=canonical_req.model,
                                    provider_id=target.provider_config.id,
                                    account_id=target.account_id,
                                    status=200,
                                    duration_ms=_elapsed_ms(),
                                    streamed=True,
                                    request_body=logged_request_body,
                                )
                            if stream_ok:
                                handler.mark_success(target.account_id, target.model)

                    return StreamingResponse(_native_stream(), media_type=native_media)

                passthrough_usage = Usage(input_tokens=0, output_tokens=0)
                if native_result.json_data:
                    try:
                        passthrough_usage = client_adapter.parse_upstream_response(
                            native_result.json_data
                        ).usage
                    except Exception:
                        pass
                await record_usage(
                    db_path,
                    provider_id=target.provider_config.id,
                    model=target.model,
                    account_id=target.account_id,
                    input_tokens=passthrough_usage.input_tokens,
                    output_tokens=passthrough_usage.output_tokens,
                    cache_creation_tokens=passthrough_usage.cache_creation_input_tokens,
                    cache_read_tokens=passthrough_usage.cache_read_input_tokens,
                    status=200,
                    client_key_id=client_key_id,
                    client_key_label=client_key_label,
                    cost=compute_cost(passthrough_usage, target.model, pricing_registry),
                )
                handler.record_quota_tokens(
                    target,
                    passthrough_usage.input_tokens + passthrough_usage.output_tokens,
                )
                if log_requests:
                    try:
                        native_response_body: str | None = json.dumps(
                            native_result.json_data, ensure_ascii=False
                        )
                    except (TypeError, ValueError):
                        native_response_body = str(native_result.json_data)
                    await record_request_log(
                        db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=200,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        response_body=native_response_body,
                    )
                handler.mark_success(target.account_id, target.model)
                return JSONResponse(
                    content=native_result.json_data if native_result.json_data else {}
                )
        # ── End native passthrough ─────────────────────────────────────

        provider_adapter = _resolve_format(target.native_format)
        upstream_payload = provider_adapter.build_upstream_request(attempt_req, upstream_model)
        apply_thinking_to_payload(
            upstream_payload,
            target_format=target.native_format,
            model=upstream_model,
            caps=model_caps,
            intent=attempt_thinking,
        )
        inject_reasoning_content(upstream_payload, provider=target.prefix, model=target.model)
        upstream_payload = _apply_client_body_quirks(
            upstream_payload,
            client_format=client_format,
            client_tool=client_tool,
            model=upstream_model,
            provider_prefix=target.prefix,
        )
        providers: dict[str, Provider] = request.app.state.providers
        provider = providers[target.provider_config.id]
        handler.record_attempt(target)

        try:
            if attempt_req.stream:
                result = await provider.call(upstream_payload, stream=True)
                if result.status_code >= 400:
                    if is_fallback_eligible(result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(result.status_code).value,
                            model=target.model,
                            retry_after=result.retry_after,
                        )
                        last_error = f"{target.account_id}: {result.status_code}"
                        continue
                    await _log_error_and_raise(
                        log_requests=log_requests,
                        db_path=db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=result.status_code,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        detail=(str(result.json_data) if result.json_data else "Upstream error"),
                        response_body=result.json_data,
                    )
                lines = result.lines
                if lines is None:
                    await _log_error_and_raise(
                        log_requests=log_requests,
                        db_path=db_path,
                        client_format=client_format,
                        model=canonical_req.model,
                        provider_id=target.provider_config.id,
                        account_id=target.account_id,
                        status=502,
                        duration_ms=_elapsed_ms(),
                        request_body=logged_request_body,
                        detail="No stream from upstream",
                    )
                parser = provider_adapter.stream_parser()
                emitter = client_adapter.stream_emitter()
                tracker = StreamUsageTracker(parser)

                async def _streaming_generator() -> AsyncIterator[bytes]:
                    stream_ok = False
                    try:
                        async for chunk in translate_stream(lines, tracker, emitter):
                            yield chunk
                        stream_ok = True
                    finally:
                        usage = tracker.get_usage()
                        cost = compute_cost(usage, target.model, pricing_registry)
                        handler.record_quota_tokens(
                            target, usage.input_tokens + usage.output_tokens
                        )
                        await record_usage(
                            db_path,
                            provider_id=target.provider_config.id,
                            model=target.model,
                            account_id=target.account_id,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cache_creation_tokens=usage.cache_creation_input_tokens,
                            cache_read_tokens=usage.cache_read_input_tokens,
                            status=200,
                            client_key_id=client_key_id,
                            client_key_label=client_key_label,
                            cost=cost,
                        )
                        if log_requests:
                            await record_request_log(
                                db_path,
                                client_format=client_format,
                                model=canonical_req.model,
                                provider_id=target.provider_config.id,
                                account_id=target.account_id,
                                status=200,
                                duration_ms=_elapsed_ms(),
                                streamed=True,
                                request_body=logged_request_body,
                            )
                        if stream_ok:
                            handler.mark_success(target.account_id, target.model)

                media_type = getattr(client_adapter, "stream_media_type", "text/event-stream")
                return StreamingResponse(_streaming_generator(), media_type=media_type)

            result = await provider.call(upstream_payload, stream=False)
            if result.status_code >= 400:
                if is_fallback_eligible(result.status_code):
                    handler.mark_cooldown(
                        target.account_id,
                        classify_error(result.status_code).value,
                        model=target.model,
                        retry_after=result.retry_after,
                    )
                    last_error = f"{target.account_id}: {result.status_code}"
                    continue
                await _log_error_and_raise(
                    log_requests=log_requests,
                    db_path=db_path,
                    client_format=client_format,
                    model=canonical_req.model,
                    provider_id=target.provider_config.id,
                    account_id=target.account_id,
                    status=result.status_code,
                    duration_ms=_elapsed_ms(),
                    request_body=logged_request_body,
                    detail=(str(result.json_data) if result.json_data else "Upstream error"),
                    response_body=result.json_data,
                )
            if result.json_data is None:
                await _log_error_and_raise(
                    log_requests=log_requests,
                    db_path=db_path,
                    client_format=client_format,
                    model=canonical_req.model,
                    provider_id=target.provider_config.id,
                    account_id=target.account_id,
                    status=502,
                    duration_ms=_elapsed_ms(),
                    request_body=logged_request_body,
                    detail="Empty upstream response",
                )
            canonical_resp = provider_adapter.parse_upstream_response(result.json_data)
            client_payload = client_adapter.emit_response(canonical_resp)

            cost = compute_cost(canonical_resp.usage, target.model, pricing_registry)
            handler.record_quota_tokens(
                target,
                canonical_resp.usage.input_tokens + canonical_resp.usage.output_tokens,
            )
            await record_usage(
                db_path,
                provider_id=target.provider_config.id,
                model=target.model,
                account_id=target.account_id,
                input_tokens=canonical_resp.usage.input_tokens,
                output_tokens=canonical_resp.usage.output_tokens,
                cache_creation_tokens=canonical_resp.usage.cache_creation_input_tokens,
                cache_read_tokens=canonical_resp.usage.cache_read_input_tokens,
                status=result.status_code,
                client_key_id=client_key_id,
                client_key_label=client_key_label,
                cost=cost,
            )

            if log_requests:
                try:
                    logged_response_body: str | None = json.dumps(
                        client_payload, ensure_ascii=False
                    )
                except (TypeError, ValueError):
                    logged_response_body = str(client_payload)
                await record_request_log(
                    db_path,
                    client_format=client_format,
                    model=canonical_req.model,
                    provider_id=target.provider_config.id,
                    account_id=target.account_id,
                    status=result.status_code,
                    duration_ms=_elapsed_ms(),
                    request_body=logged_request_body,
                    response_body=logged_response_body,
                )

            handler.mark_success(target.account_id, target.model)
            return JSONResponse(content=client_payload)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            handler.mark_cooldown(target.account_id, "network", model=target.model)
            last_error = f"{target.account_id}: {type(e).__name__}"
            continue

    if log_requests:
        await record_request_log(
            db_path,
            client_format=client_format,
            model=canonical_req.model,
            status=503,
            duration_ms=_elapsed_ms(),
            request_body=logged_request_body,
            error=f"All providers exhausted: {last_error}",
        )
    raise HTTPException(status_code=503, detail=f"All providers exhausted: {last_error}")


@router.get("/models", dependencies=[Depends(require_api_key)])
async def list_models(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    allowed = key_allowed_models(request)
    data: list[dict[str, Any]] = []
    for prefix, configs in registry.providers.items():
        models_seen: set[str] = set()
        for config in configs:
            for model in config.models:
                if model not in models_seen:
                    models_seen.add(model)
                    model_id = f"{prefix}/{model}"
                    if not model_allowed(model_id, allowed):
                        continue
                    data.append(
                        {
                            "id": model_id,
                            "object": "model",
                            "created": 0,
                            "owned_by": config.id,
                        }
                    )
    for combo_name in registry.combos:
        if not model_allowed(combo_name, allowed):
            continue
        data.append(
            {
                "id": combo_name,
                "object": "model",
                "created": 0,
                "owned_by": "combo",
            }
        )
    return {"object": "list", "data": data}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("openai", body, request)


@router.post("/responses", dependencies=[Depends(require_api_key)])
async def responses(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("openai_responses", body, request)


@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("anthropic", body, request)


gemini_router = APIRouter()


@gemini_router.post("/v1beta/models/{model_action:path}", dependencies=[Depends(require_api_key)])
async def gemini_generate(model_action: str, request: Request) -> Response:
    if ":" not in model_action:
        raise HTTPException(status_code=404, detail="Unknown endpoint")
    model, action = model_action.rsplit(":", 1)
    if action not in ("generateContent", "streamGenerateContent"):
        raise HTTPException(status_code=404, detail=f"Unsupported action: {action}")
    body: dict[str, Any] = await request.json()
    body["model"] = model
    body["stream"] = action == "streamGenerateContent"
    return await _handle("gemini", body, request)


ollama_router = APIRouter()


def _ollama_model_entries(
    registry: ProviderRegistry,
    allowed_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prefix, configs in registry.providers.items():
        for config in configs:
            for model in config.models:
                name = f"{prefix}/{model}"
                if name in seen or not model_allowed(name, allowed_models):
                    continue
                seen.add(name)
                models.append(
                    {
                        "name": name,
                        "model": name,
                        "modified_at": now,
                        "size": 0,
                        "digest": "",
                        "details": {"family": "janus", "format": "gateway"},
                    }
                )
    for combo_name in registry.combos:
        if not model_allowed(combo_name, allowed_models):
            continue
        models.append(
            {
                "name": combo_name,
                "model": combo_name,
                "modified_at": now,
                "size": 0,
                "digest": "",
                "details": {"family": "janus", "format": "combo"},
            }
        )
    return models


def _ollama_generate_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    prompt = body.get("prompt") or ""
    user_msg: dict[str, Any] = {"role": "user", "content": prompt}
    if body.get("images"):
        user_msg["images"] = body["images"]
    chat: dict[str, Any] = {
        "model": body.get("model"),
        "messages": [user_msg],
        "stream": body.get("stream", True),
    }
    if body.get("options") is not None:
        chat["options"] = body["options"]
    return chat


def _ollama_chat_json_to_generate(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    message = out.pop("message", None) or {}
    out["response"] = message.get("content") or ""
    return out


def _ollama_chat_ndjson_to_generate(line: str) -> str:
    raw = line.strip()
    if not raw:
        return line
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return line
    if "message" in obj:
        msg = obj.pop("message") or {}
        obj["response"] = msg.get("content") or ""
    return json.dumps(obj, ensure_ascii=False) + "\n"


@ollama_router.post("/api/chat", dependencies=[Depends(require_api_key)])
async def ollama_chat(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("ollama", body, request)


@ollama_router.post("/api/generate", dependencies=[Depends(require_api_key)])
async def ollama_generate(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    if not body.get("model"):
        raise HTTPException(status_code=400, detail="model required")
    chat_body = _ollama_generate_to_chat(body)
    response = await _handle("ollama", chat_body, request)
    if isinstance(response, StreamingResponse):
        async def _remap() -> AsyncIterator[bytes]:
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                for part in text.splitlines(keepends=True):
                    if not part.strip():
                        continue
                    line = part if part.endswith("\n") else part + "\n"
                    yield _ollama_chat_ndjson_to_generate(line).encode()

        return StreamingResponse(
            _remap(),
            media_type=response.media_type or "application/x-ndjson",
            status_code=response.status_code,
        )
    if isinstance(response, JSONResponse):
        data = json.loads(bytes(response.body).decode())
        return JSONResponse(
            content=_ollama_chat_json_to_generate(data), status_code=response.status_code
        )
    try:
        raw = bytes(response.body) if hasattr(response, "body") else b""
        if raw:
            data = json.loads(raw.decode())
            return JSONResponse(
                content=_ollama_chat_json_to_generate(data), status_code=response.status_code
            )
    except Exception:
        pass
    return response


@ollama_router.get("/api/tags", dependencies=[Depends(require_api_key)])
async def ollama_tags(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    return {"models": _ollama_model_entries(registry, key_allowed_models(request))}


@ollama_router.post("/api/show", dependencies=[Depends(require_api_key)])
async def ollama_show(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    name = (body.get("name") or body.get("model") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="model name required")
    registry: ProviderRegistry = request.app.state.registry
    entries = _ollama_model_entries(registry, key_allowed_models(request))
    match = next((e for e in entries if e["name"] == name), None)
    if match is None:
        return JSONResponse(
            content={"error": f"model '{name}' not found"},
            status_code=404,
        )
    details: dict[str, Any] = {
        "parent_model": "",
        "format": "gguf",
        "family": "janus",
        "families": ["janus"],
        "parameter_size": "N/A",
        "quantization_level": "gateway",
    }
    entry_details = match.get("details") or {}
    if entry_details.get("format"):
        details["format"] = entry_details["format"]
    if entry_details.get("family"):
        details["family"] = entry_details["family"]
        details["families"] = [entry_details["family"]]
    return JSONResponse(
        content={
            "modelfile": "",
            "parameters": "",
            "template": "{{ .Prompt }}",
            "details": details,
            "model_info": {},
            "capabilities": ["completion"],
        }
    )


@ollama_router.get("/api/version")
async def ollama_version() -> dict[str, str]:
    return {"version": "0.6.0"}
