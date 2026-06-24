from __future__ import annotations

from typing import Any

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
from janus.streaming.translator import translate_stream

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
    registry: ProviderRegistry,
) -> Response:
    client_adapter = FORMATS[client_format]
    canonical_req = client_adapter.parse_request(body)

    target = registry.lookup(canonical_req.model)
    if target is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown model: {canonical_req.model}"
        )

    provider_adapter = _resolve_format(target.native_format)
    upstream_payload = provider_adapter.build_upstream_request(canonical_req, target.model)

    provider = _build_provider(target.provider_config)

    if canonical_req.stream:
        result = await provider.call(upstream_payload, stream=True)
        lines = result.lines
        if lines is None:
            raise HTTPException(status_code=502, detail="No stream from upstream")
        parser = provider_adapter.stream_parser()
        emitter = client_adapter.stream_emitter()
        generator = translate_stream(lines, parser, emitter)
        return StreamingResponse(generator, media_type="text/event-stream")

    result = await provider.call(upstream_payload, stream=False)
    if result.status_code >= 400:
        raise HTTPException(
            status_code=result.status_code,
            detail=str(result.json_data) if result.json_data else "Upstream error",
        )
    if result.json_data is None:
        raise HTTPException(status_code=502, detail="Empty upstream response")

    canonical_resp = provider_adapter.parse_upstream_response(result.json_data)
    client_payload = client_adapter.emit_response(canonical_resp)
    return JSONResponse(content=client_payload)


@router.get("/models", dependencies=[Depends(require_api_key)])
async def list_models(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    data: list[dict[str, Any]] = []
    for prefix, config in registry.providers.items():
        for model in config.models:
            data.append(
                {
                    "id": f"{prefix}/{model}",
                    "object": "model",
                    "created": 0,
                    "owned_by": config.id,
                }
            )
    return {"object": "list", "data": data}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    registry: ProviderRegistry = request.app.state.registry
    return await _handle("openai", body, registry)


@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    registry: ProviderRegistry = request.app.state.registry
    return await _handle("anthropic", body, registry)
