from __future__ import annotations

import datetime
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from janus.canonical.tool_calls import prepare_tool_messages
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.base import FormatAdapter
from janus.formats.gemini import GeminiAdapter
from janus.formats.ollama import OllamaAdapter
from janus.formats.openai import OpenAIAdapter
from janus.formats.openai_responses import OpenAIResponsesAdapter
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.capabilities import detect_required_capabilities
from janus.routing.errors import classify_error, is_fallback_eligible
from janus.routing.fallback import AccountStrategy, FallbackHandler
from janus.storage.budgets import get_budget_status
from janus.storage.request_logs import record_request_log
from janus.streaming.translator import translate_stream
from janus.streaming.usage import StreamUsageTracker
from janus.tokensavers.pipeline import SaverPipeline

from .deps import require_api_key

router = APIRouter()

FORMATS: dict[str, FormatAdapter] = {
    "openai": OpenAIAdapter(),
    "openai_responses": OpenAIResponsesAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
    "ollama": OllamaAdapter(),
}


def _resolve_format(name: str) -> FormatAdapter:
    if name in ("opencode_free", "github_copilot"):
        name = "openai"
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
    except Exception:
        pass
    return None


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

    saver_pipeline: SaverPipeline = request.app.state.saver_pipeline
    canonical_req = await saver_pipeline.apply_async(canonical_req)
    canonical_req = saver_pipeline.apply(canonical_req)
    canonical_req = canonical_req.model_copy(
        update={"messages": prepare_tool_messages(canonical_req.messages)},
    )
    specific_model = (
        canonical_req.model.split("/", 1)[1] if "/" in canonical_req.model else canonical_req.model
    )

    from janus.storage.settings import (
        get_all_settings,
        request_logging_enabled,
        resolve_account_strategy,
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    last_error = "Unknown error"
    for target in attempts:
        provider_adapter = _resolve_format(target.native_format)
        upstream_payload = provider_adapter.build_upstream_request(canonical_req, target.model)
        providers: dict[str, Provider] = request.app.state.providers
        provider = providers[target.provider_config.id]
        handler.record_attempt(target)

        try:
            if canonical_req.stream:
                result = await provider.call(upstream_payload, stream=True)
                if result.status_code >= 400:
                    if is_fallback_eligible(result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(result.status_code).value,
                            model=specific_model,
                            retry_after=result.retry_after,
                        )
                        last_error = f"{target.account_id}: {result.status_code}"
                        continue
                    raise HTTPException(
                        status_code=result.status_code,
                        detail=(str(result.json_data) if result.json_data else "Upstream error"),
                    )
                lines = result.lines
                if lines is None:
                    raise HTTPException(status_code=502, detail="No stream from upstream")
                parser = provider_adapter.stream_parser()
                emitter = client_adapter.stream_emitter()
                tracker = StreamUsageTracker(parser)

                from janus.pricing.calculator import compute_cost
                from janus.storage.usage import record_usage

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
                            handler.mark_success(target.account_id, specific_model)

                media_type = getattr(client_adapter, "stream_media_type", "text/event-stream")
                return StreamingResponse(_streaming_generator(), media_type=media_type)

            result = await provider.call(upstream_payload, stream=False)
            if result.status_code >= 400:
                if is_fallback_eligible(result.status_code):
                    handler.mark_cooldown(
                        target.account_id,
                        classify_error(result.status_code).value,
                        model=specific_model,
                        retry_after=result.retry_after,
                    )
                    last_error = f"{target.account_id}: {result.status_code}"
                    continue
                raise HTTPException(
                    status_code=result.status_code,
                    detail=(str(result.json_data) if result.json_data else "Upstream error"),
                )
            if result.json_data is None:
                raise HTTPException(status_code=502, detail="Empty upstream response")
            canonical_resp = provider_adapter.parse_upstream_response(result.json_data)
            client_payload = client_adapter.emit_response(canonical_resp)

            from janus.pricing.calculator import compute_cost
            from janus.storage.usage import record_usage

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

            handler.mark_success(target.account_id, specific_model)
            return JSONResponse(content=client_payload)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            handler.mark_cooldown(target.account_id, "network", model=specific_model)
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
    data: list[dict[str, Any]] = []
    for prefix, configs in registry.providers.items():
        models_seen: set[str] = set()
        for config in configs:
            for model in config.models:
                if model not in models_seen:
                    models_seen.add(model)
                    data.append(
                        {
                            "id": f"{prefix}/{model}",
                            "object": "model",
                            "created": 0,
                            "owned_by": config.id,
                        }
                    )
    for combo_name in registry.combos:
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


@ollama_router.post("/api/chat", dependencies=[Depends(require_api_key)])
async def ollama_chat(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("ollama", body, request)


@ollama_router.get("/api/tags", dependencies=[Depends(require_api_key)])
async def ollama_tags(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    now = datetime.datetime.now(datetime.UTC).isoformat()
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prefix, configs in registry.providers.items():
        for config in configs:
            for model in config.models:
                name = f"{prefix}/{model}"
                if name not in seen:
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
    return {"models": models}


@ollama_router.get("/api/version")
async def ollama_version() -> dict[str, str]:
    return {"version": "0.6.0"}
