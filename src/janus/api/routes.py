from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from janus.config.schema import ProviderConfig
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.base import FormatAdapter
from janus.formats.gemini import GeminiAdapter
from janus.formats.openai import OpenAIAdapter
from janus.providers.anthropic import AnthropicProvider
from janus.providers.base import Provider
from janus.providers.gemini import GeminiProvider
from janus.providers.openai_compat import OpenAICompatProvider
from janus.providers.opencode_free import OpenCodeFreeProvider
from janus.providers.registry import ProviderRegistry
from janus.routing.errors import classify_error, is_fallback_eligible
from janus.routing.fallback import FallbackHandler
from janus.streaming.translator import translate_stream
from janus.tokensavers.pipeline import SaverPipeline

from .deps import require_api_key

router = APIRouter()

FORMATS: dict[str, FormatAdapter] = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}


def _resolve_format(name: str) -> FormatAdapter:
    if name == "opencode_free":
        name = "openai"
    return FORMATS[name]


def _build_provider(config: ProviderConfig) -> Provider:
    if config.api_type == "opencode_free":
        return OpenCodeFreeProvider()
    if config.api_type == "openai_compat":
        return OpenAICompatProvider(base_url=config.base_url, api_key=config.api_key)
    if config.api_type == "anthropic":
        return AnthropicProvider(api_key=config.api_key or "", base_url=config.base_url)
    if config.api_type == "gemini":
        return GeminiProvider(api_key=config.api_key or "")
    raise ValueError(f"Unknown api_type: {config.api_type}")


async def _handle(
    client_format: str,
    body: dict[str, Any],
    request: Request,
) -> Response:
    handler: FallbackHandler = request.app.state.fallback_handler

    client_adapter = FORMATS[client_format]
    canonical_req = client_adapter.parse_request(body)

    saver_pipeline: SaverPipeline = request.app.state.saver_pipeline
    canonical_req = saver_pipeline.apply(canonical_req)

    try:
        attempts = handler.resolve_attempts(canonical_req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    last_error = "Unknown error"
    for target in attempts:
        provider_adapter = _resolve_format(target.native_format)
        upstream_payload = provider_adapter.build_upstream_request(canonical_req, target.model)
        provider = _build_provider(target.provider_config)

        try:
            if canonical_req.stream:
                result = await provider.call(upstream_payload, stream=True)
                if result.status_code >= 400:
                    if is_fallback_eligible(result.status_code):
                        handler.mark_cooldown(
                            target.account_id,
                            classify_error(result.status_code).value,
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
                generator = translate_stream(lines, parser, emitter)
                return StreamingResponse(generator, media_type="text/event-stream")

            result = await provider.call(upstream_payload, stream=False)
            if result.status_code >= 400:
                if is_fallback_eligible(result.status_code):
                    handler.mark_cooldown(
                        target.account_id,
                        classify_error(result.status_code).value,
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
            return JSONResponse(content=client_payload)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            handler.mark_cooldown(target.account_id, "network")
            last_error = f"{target.account_id}: {type(e).__name__}"
            continue

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


@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("anthropic", body, request)
