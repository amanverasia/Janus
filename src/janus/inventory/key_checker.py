from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx

from janus.inventory.catalog import get_inventory_provider
from janus.inventory.model_catalog import enrich_model_with_catalog
from janus.inventory.url_guard import BlockedUrlError, safe_fetch
from janus.storage.upstream_keys import (
    get_upstream_key,
    record_upstream_key_history,
    update_upstream_key,
)
from janus.storage.upstream_models import replace_models_for_key

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


HealthStatus = Literal["healthy", "warning", "critical", "exhausted"]
UsabilityStatus = Literal[
    "usable", "no_quota", "rate_limited", "restricted", "unknown", "auth_failed"
]

CREDIT_WARNING_THRESHOLD = float(os.environ.get("CREDIT_WARNING_THRESHOLD", "5"))
CREDIT_CRITICAL_THRESHOLD = float(os.environ.get("CREDIT_CRITICAL_THRESHOLD", "1"))
CREDIT_PCT_WARNING = float(os.environ.get("CREDIT_PCT_WARNING", "20"))
CREDIT_PCT_CRITICAL = float(os.environ.get("CREDIT_PCT_CRITICAL", "5"))
RPM_WARNING_THRESHOLD = int(os.environ.get("RPM_WARNING_THRESHOLD", "5"))
TPM_WARNING_THRESHOLD = int(os.environ.get("TPM_WARNING_THRESHOLD", "1000"))
USABILITY_PROBE_ENABLED = os.environ.get("USABILITY_PROBE", "true").lower() != "false"
CHECK_CONCURRENCY = int(os.environ.get("CHECK_CONCURRENCY", "8"))
FETCH_TIMEOUT = 15.0

CHAT_PROBE_COMPATIBLE = {
    "openai",
    "groq",
    "together",
    "mistral",
    "deepseek",
    "xai",
    "fireworks",
    "nvidia",
    "moonshot",
    "dashscope",
    "minimax",
    "siliconflow",
    "stepfun",
    "perplexity",
    "zhipu",
    "custom",
    "anthropic",
}

CHAT_VALIDATED_PROVIDERS = {"perplexity", "nvidia", "zhipu"}

OPENAI_COMPAT_PROVIDERS = {
    "openai",
    "groq",
    "together",
    "mistral",
    "deepseek",
    "xai",
    "fireworks",
    "openrouter",
    "nvidia",
    "moonshot",
    "dashscope",
    "minimax",
    "siliconflow",
    "stepfun",
    "custom",
}

CHEAP_PROBE_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-nano",
    "anthropic": "claude-3-5-haiku-20241022",
    "groq": "llama-3.1-8b-instant",
    "deepseek": "deepseek-chat",
    "xai": "grok-2-1212",
    "mistral": "mistral-small-latest",
    "together": "meta-llama/Llama-3.2-3B-Instruct-Turbo",
    "fireworks": "accounts/fireworks/models/llama-v3p1-8b-instruct",
    "nvidia": "meta/llama-3.1-8b-instruct",
    "moonshot": "moonshot-v1-8k",
    "dashscope": "qwen-turbo",
    "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
    "stepfun": "step-1-flash",
}

CHAT_PROBE: dict[str, dict[str, Any]] = {
    "perplexity": {
        "model": "sonar",
        "models": [
            {"model_id": "sonar-pro", "display_name": "Sonar Pro"},
            {"model_id": "sonar", "display_name": "Sonar"},
            {"model_id": "sonar-reasoning-pro", "display_name": "Sonar Reasoning Pro"},
            {"model_id": "sonar-reasoning", "display_name": "Sonar Reasoning"},
            {"model_id": "sonar-deep-research", "display_name": "Sonar Deep Research"},
        ],
    },
    "nvidia": {"model": "meta/llama-3.1-8b-instruct"},
    "zhipu": {
        "model": "glm-4-flash",
        "models": [
            {"model_id": "glm-4-flash", "display_name": "GLM-4-Flash"},
            {"model_id": "glm-4.5", "display_name": "GLM-4.5"},
            {"model_id": "glm-4.5-air", "display_name": "GLM-4.5-Air"},
            {"model_id": "glm-4-plus", "display_name": "GLM-4-Plus"},
        ],
    },
}


def _finite_or_undef(value: Any) -> float | None:
    if isinstance(value, (int, float)) and float(value) == float(value):
        return float(value)
    return None


def _sanitize_numeric_fields(result: dict[str, Any]) -> None:
    for field in (
        "credits_remaining",
        "credits_total",
        "credits_used",
        "rate_limit_rpm",
        "rate_limit_tpm",
        "rate_limit_rpd",
    ):
        if field in result:
            result[field] = _finite_or_undef(result.get(field))


def compute_health_status(result: dict[str, Any]) -> None:
    _sanitize_numeric_fields(result)
    warnings: list[str] = []
    order = {"healthy": 0, "warning": 1, "critical": 2, "exhausted": 3}
    worst: HealthStatus = "healthy"

    def escalate(level: HealthStatus) -> None:
        nonlocal worst
        if order[level] > order[worst]:
            worst = level

    credits_remaining = result.get("credits_remaining")
    if credits_remaining is not None:
        if credits_remaining <= 0:
            warnings.append("Credits exhausted ($0.00 remaining)")
            escalate("exhausted")
        elif credits_remaining <= CREDIT_CRITICAL_THRESHOLD:
            warnings.append(f"Credits critically low (${credits_remaining:.2f} remaining)")
            escalate("critical")
        elif credits_remaining <= CREDIT_WARNING_THRESHOLD:
            warnings.append(f"Credits running low (${credits_remaining:.2f} remaining)")
            escalate("warning")

    credits_total = result.get("credits_total")
    if credits_remaining is not None and credits_total is not None and credits_total > 0:
        pct = (credits_remaining / credits_total) * 100
        if pct <= CREDIT_PCT_CRITICAL:
            warnings.append(f"Only {pct:.1f}% credits remaining")
            escalate("critical")
        elif pct <= CREDIT_PCT_WARNING:
            warnings.append(f"{pct:.1f}% credits remaining")
            escalate("warning")

    rate_limit_rpm = result.get("rate_limit_rpm")
    if rate_limit_rpm is not None and rate_limit_rpm <= RPM_WARNING_THRESHOLD:
        warnings.append(f"Very low rate limit: {rate_limit_rpm} RPM")
        escalate("critical" if rate_limit_rpm <= 1 else "warning")

    rate_limit_tpm = result.get("rate_limit_tpm")
    if rate_limit_tpm is not None and rate_limit_tpm <= TPM_WARNING_THRESHOLD:
        warnings.append(f"Very low token limit: {rate_limit_tpm} TPM")
        escalate("critical" if rate_limit_tpm <= 100 else "warning")

    rate_limit_rpd = result.get("rate_limit_rpd")
    if rate_limit_rpd is not None and rate_limit_rpd <= 50:
        warnings.append(f"Very low daily limit: {rate_limit_rpd} requests/day")
        escalate("critical" if rate_limit_rpd <= 10 else "warning")

    result["health_status"] = worst
    result["health_warnings"] = warnings


async def _fetch_with_headers(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    content = json.dumps(body).encode() if body is not None else None
    try:
        response = await safe_fetch(
            url,
            method=method,
            headers=headers,
            content=content,
            timeout=FETCH_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise TimeoutError("Connection timeout") from exc

    response_headers = {key.lower(): value for key, value in response.headers.items()}
    parsed_body: Any = None
    try:
        parsed_body = response.json()
    except Exception:
        parsed_body = None
    return {"status": response.status_code, "headers": response_headers, "body": parsed_body}


def _extract_rate_limits(headers: dict[str, str]) -> dict[str, int | None]:
    result: dict[str, int | None] = {"rpm": None, "tpm": None, "rpd": None}
    rpm_keys = [
        "x-ratelimit-limit-requests",
        "x-ratelimit-requests-limit",
        "ratelimit-requests-limit",
    ]
    tpm_keys = [
        "x-ratelimit-limit-tokens",
        "x-ratelimit-tokens-limit",
        "ratelimit-tokens-limit",
    ]
    rpd_keys = ["x-ratelimit-requests-per-day", "x-ratelimit-limit-requests-per-day"]

    for key in rpm_keys:
        if headers.get(key):
            result["rpm"] = int(headers[key])
            break
    for key in tpm_keys:
        if headers.get(key):
            result["tpm"] = int(headers[key])
            break
    for key in rpd_keys:
        if headers.get(key):
            result["rpd"] = int(headers[key])
            break

    combined = headers.get("x-ratelimit-limit")
    if combined and result["rpm"] is None and result["tpm"] is None:
        for part in combined.split(","):
            part = part.strip()
            match = re.search(r"(\d+)", part)
            if not match:
                continue
            value = int(match.group(1))
            if "req" in part:
                result["rpm"] = value
            elif "tok" in part:
                result["tpm"] = value
    return result


def _extract_openrouter_credits(headers: dict[str, str]) -> dict[str, float | None]:
    remaining = headers.get("x-credits-remaining")
    used = headers.get("x-credits-used")
    limit = headers.get("x-credits-limit")
    return {
        "remaining": float(remaining) if remaining else None,
        "used": float(used) if used else None,
        "total": float(limit) if limit else None,
    }


def _parse_openai_compat_models(body: Any) -> list[dict[str, Any]]:
    raw = None
    if isinstance(body, dict):
        raw = body.get("data") or body.get("models")
    data = raw if isinstance(raw, list) else (body if isinstance(body, list) else [])
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pricing_raw = item.get("pricing")
        pricing_dict: dict[str, Any] = pricing_raw if isinstance(pricing_raw, dict) else {}
        models.append(
            {
                "model_id": item["id"],
                "display_name": item["id"],
                "context_window": item.get("context_window"),
                "max_output_tokens": item.get("max_output_tokens")
                or item.get("max_completion_tokens"),
                "pricing_input": pricing_dict.get("prompt"),
                "pricing_output": pricing_dict.get("completion"),
                "capabilities": json.dumps(item["capabilities"])
                if item.get("capabilities") is not None
                else None,
            }
        )
    return models


def _parse_anthropic_models(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    data = body.get("data") or body.get("models") or []
    if not isinstance(data, list):
        return []
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        models.append(
            {
                "model_id": item["id"],
                "display_name": item.get("display_name") or item["id"],
                "context_window": item.get("context_window"),
                "max_output_tokens": item.get("max_output_tokens"),
                "pricing_input": None,
                "pricing_output": None,
                "capabilities": None,
            }
        )
    return models


def _get_headers_for_provider(provider: dict[str, Any], key: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    auth_prefix = provider.get("auth_prefix") or ""
    auth_header = provider.get("auth_header") or "Authorization"
    if auth_prefix:
        headers[auth_header] = f"{auth_prefix} {key}"
    else:
        headers[auth_header] = key
    if provider["id"] == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _get_base_url(provider: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    base_url = provider.get("base_url") or ""
    if not base_url and metadata and metadata.get("custom_base_url"):
        base_url = str(metadata["custom_base_url"]).rstrip("/")
    return base_url


def _get_check_url(provider: dict[str, Any], key: str, metadata: dict[str, Any] | None) -> str:
    base_url = _get_base_url(provider, metadata)
    endpoint = provider.get("health_check_endpoint") or provider.get("models_endpoint") or "/models"
    if provider["id"] == "google":
        return f"{base_url}{endpoint}?key={quote(key)}"
    if provider["id"] == "huggingface":
        return "https://huggingface.co/api/whoami-v2"
    return f"{base_url}{endpoint}"


def _parse_credit_check_response(
    provider_id: str,
    body: Any,
    result: dict[str, Any],
) -> None:
    if provider_id == "openrouter" and isinstance(body, dict):
        data_raw = body.get("data")
        data: dict[str, Any] = data_raw if isinstance(data_raw, dict) else body
        if data.get("limit") is not None:
            result["credits_total"] = float(data["limit"])
        if data.get("limit_remaining") is not None:
            result["credits_remaining"] = float(data["limit_remaining"])
        if data.get("usage") is not None:
            result["credits_used"] = float(data["usage"])
        meta: dict[str, Any] = {}
        for field in (
            "is_free_tier",
            "usage_daily",
            "usage_weekly",
            "usage_monthly",
            "limit_reset",
        ):
            if data.get(field) is not None:
                meta[field] = data[field]
        if meta:
            result["metadata"] = meta

    if provider_id == "deepseek" and isinstance(body, dict):
        infos = body.get("balance_infos")
        if isinstance(infos, list):
            usd = next((item for item in infos if item.get("currency") == "USD"), None)
            info = usd or (infos[0] if infos else None)
            if isinstance(info, dict):
                total = float(str(info.get("total_balance") or "0"))
                granted = float(str(info.get("granted_balance") or "0"))
                topped = float(str(info.get("topped_up_balance") or "0"))
                result["credits_remaining"] = total
                result["credits_total"] = granted + topped
                result["credits_used"] = max(0.0, result["credits_total"] - total)
                if not usd and info.get("currency"):
                    result["metadata"] = {"currency": info["currency"]}

    if provider_id == "moonshot" and isinstance(body, dict):
        if body.get("code") not in (None, 0):
            return
        moonshot_data = body.get("data")
        if isinstance(moonshot_data, dict) and moonshot_data.get("available_balance") is not None:
            remaining = float(moonshot_data["available_balance"])
            cash = float(moonshot_data.get("cash_balance") or 0)
            voucher = float(moonshot_data.get("voucher_balance") or 0)
            result["credits_remaining"] = remaining
            result["credits_total"] = cash + voucher
            result["credits_used"] = max(0.0, result["credits_total"] - remaining)
            if cash or voucher:
                result["metadata"] = {"cash_balance": cash, "voucher_balance": voucher}


def _get_probe_model(provider_id: str, models: list[dict[str, Any]] | None) -> str | None:
    if provider_id in CHEAP_PROBE_MODELS:
        return CHEAP_PROBE_MODELS[provider_id]
    if models:
        model_id = models[0].get("model_id")
        return str(model_id) if model_id is not None else None
    return None


async def _probe_usability(
    key_value: str,
    provider: dict[str, Any],
    metadata: dict[str, Any] | None,
    result: dict[str, Any],
) -> None:
    provider_id = provider["id"]
    if provider_id == "openrouter":
        remaining = result.get("credits_remaining")
        if remaining is not None and remaining <= 0:
            result["is_usable"] = False
            result["usability_status"] = "no_quota"
            result["usability_note"] = "No credits remaining"
        else:
            result["is_usable"] = True
            result["usability_status"] = "usable"
            result["usability_note"] = (
                f"${remaining:.2f} credits available"
                if remaining is not None
                else "Account-linked (no prepaid cap)"
            )
        return

    if provider_id not in CHAT_PROBE_COMPATIBLE:
        result["is_usable"] = result.get("is_valid", False)
        result["usability_status"] = "unknown"
        result["usability_note"] = "Auth OK; usability not probed for this provider"
        return

    probe_model = _get_probe_model(provider_id, result.get("models"))
    if not probe_model:
        result["is_usable"] = result.get("is_valid", False)
        result["usability_status"] = "unknown"
        result["usability_note"] = "Auth OK; usability not probed for this provider"
        return

    base_url = _get_base_url(provider, metadata)
    headers = _get_headers_for_provider(provider, key_value)
    headers["Content-Type"] = "application/json"

    if provider_id == "anthropic":
        url = f"{base_url}/messages"
        body = {
            "model": probe_model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
    else:
        url = f"{base_url}/chat/completions"
        body = {
            "model": probe_model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }

    try:
        response = await _fetch_with_headers(url, method="POST", headers=headers, body=body)
    except Exception as exc:
        result["is_usable"] = result.get("is_valid", False)
        result["usability_status"] = "unknown"
        result["usability_note"] = f"Probe failed: {exc}"
        return

    status = response["status"]
    body = response["body"]
    err_obj = body.get("error") if isinstance(body, dict) else body
    if not isinstance(err_obj, dict):
        err_obj = {}
    code = err_obj.get("code") or err_obj.get("type") or ""
    message = str(err_obj.get("message") or json.dumps(response["body"] or {}))[:200]
    combined = f"{code} {message}"

    if status in {200, 201}:
        result["is_usable"] = True
        result["usability_status"] = "usable"
        result["usability_note"] = f"Inference OK (probed {probe_model})"
    elif status in {401, 403}:
        result["is_usable"] = False
        result["usability_status"] = "auth_failed"
        result["usability_note"] = f"Auth rejected on inference: {message}"
    elif re.search(r"quota|insufficient|billing|exceeded|credit|balance", combined, re.I):
        result["is_usable"] = False
        result["usability_status"] = "no_quota"
        result["usability_note"] = message
    elif status == 429:
        result["is_usable"] = False
        result["usability_status"] = "rate_limited"
        result["usability_note"] = message or "Rate limited"
    elif status == 404 or re.search(r"model_not_found|does not exist|not found", combined, re.I):
        result["is_usable"] = True
        result["usability_status"] = "unknown"
        result["usability_note"] = "Probe model unavailable; auth + listing OK"
    else:
        result["is_usable"] = False
        result["usability_status"] = "restricted"
        result["usability_note"] = f"HTTP {status}: {message}"


async def _validate_via_chat(
    provider: dict[str, Any],
    key_value: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    base = _get_base_url(provider, metadata)
    headers = _get_headers_for_provider(provider, key_value)
    headers["Content-Type"] = "application/json"

    cfg = CHAT_PROBE.get(provider["id"], {})
    probe_model = cfg.get("model", "")
    models: list[dict[str, Any]] = list(cfg.get("models") or [])

    if not models:
        try:
            model_list = await _fetch_with_headers(f"{base}/models", headers=headers)
            if model_list["status"] in {200, 201} and model_list["body"]:
                models = _parse_openai_compat_models(model_list["body"])
        except Exception:
            pass

    if not probe_model and models:
        probe_model = models[0]["model_id"]
    if not probe_model:
        return {"is_valid": False, "error": "No model available to validate"}

    response = await _fetch_with_headers(
        f"{base}/chat/completions",
        method="POST",
        headers=headers,
        body={
            "model": probe_model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    rate_limits = _extract_rate_limits(response["headers"])

    def with_extras(check_result: dict[str, Any]) -> dict[str, Any]:
        if models:
            check_result["models"] = models
        if rate_limits["rpm"] is not None:
            check_result["rate_limit_rpm"] = rate_limits["rpm"]
        if rate_limits["tpm"] is not None:
            check_result["rate_limit_tpm"] = rate_limits["tpm"]
        if rate_limits["rpd"] is not None:
            check_result["rate_limit_rpd"] = rate_limits["rpd"]
        return check_result

    status = response["status"]
    if status in {200, 201}:
        result = with_extras(
            {
                "is_valid": True,
                "is_usable": True,
                "usability_status": "usable",
                "usability_note": f"Inference OK ({probe_model})",
            }
        )
        compute_health_status(result)
        return result
    if status in {401, 403}:
        return {"is_valid": False, "error": f"Auth failed ({status})"}
    if status == 429:
        return with_extras(
            {
                "is_valid": True,
                "is_usable": True,
                "usability_status": "rate_limited",
                "usability_note": "Rate limited",
            }
        )
    if status in {400, 404}:
        return with_extras(
            {
                "is_valid": True,
                "is_usable": False,
                "usability_status": "unknown",
                "usability_note": f"Auth OK; probe returned HTTP {status}",
            }
        )
    return {"is_valid": False, "error": f"HTTP {status}"}


def _parse_models_from_body(provider: dict[str, Any], body: Any) -> list[dict[str, Any]]:
    provider_id = provider["id"]
    if provider_id in OPENAI_COMPAT_PROVIDERS:
        return _parse_openai_compat_models(body)
    if provider_id == "cohere" and isinstance(body, dict):
        raw_models = body.get("models")
        if not isinstance(raw_models, list):
            return []
        models: list[dict[str, Any]] = []
        for item in raw_models:
            if not isinstance(item, dict) or not (item.get("name") or item.get("id")):
                continue
            model_id = item.get("name") or item.get("id")
            models.append(
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "context_window": item.get("context_length"),
                    "max_output_tokens": None,
                    "pricing_input": None,
                    "pricing_output": None,
                    "capabilities": json.dumps(item["endpoints"])
                    if isinstance(item.get("endpoints"), list)
                    else None,
                }
            )
        return models
    if provider_id == "anthropic":
        return _parse_anthropic_models(body)
    if provider_id == "google" and isinstance(body, dict):
        raw = body.get("models") or body.get("data") or []
        if not isinstance(raw, list):
            return []
        return [
            {
                "model_id": item.get("name") or item.get("id"),
                "display_name": item.get("displayName") or item.get("name") or item.get("id"),
                "context_window": item.get("inputTokenLimit") or item.get("contextWindow"),
                "max_output_tokens": item.get("outputTokenLimit") or item.get("maxOutputTokens"),
                "pricing_input": None,
                "pricing_output": None,
                "capabilities": json.dumps(item["supportedGenerationMethods"])
                if item.get("supportedGenerationMethods") is not None
                else None,
            }
            for item in raw
            if isinstance(item, dict) and (item.get("name") or item.get("id"))
        ]
    if provider_id == "replicate" and isinstance(body, dict):
        raw = body.get("results")
        if not isinstance(raw, list):
            return []
        return [
            {
                "model_id": f"{item.get('owner')}/{item.get('name')}",
                "display_name": item.get("name"),
                "context_window": None,
                "max_output_tokens": None,
                "pricing_input": None,
                "pricing_output": None,
                "capabilities": None,
            }
            for item in raw
            if isinstance(item, dict) and item.get("owner") and item.get("name")
        ]
    return []


async def validate_key(
    key_value: str,
    provider_id: str,
    metadata: dict[str, Any] | None = None,
    *,
    skip_probe: bool = False,
) -> dict[str, Any]:
    provider = get_inventory_provider(provider_id)
    if provider is None:
        return {"is_valid": False, "error": f"Unknown provider: {provider_id}"}

    try:
        if provider_id in CHAT_VALIDATED_PROVIDERS:
            return await _validate_via_chat(provider, key_value, metadata)

        url = _get_check_url(provider, key_value, metadata)
        headers = _get_headers_for_provider(provider, key_value)
        result = await _fetch_with_headers(url, headers=headers)

        if result["status"] in {200, 201}:
            check_result: dict[str, Any] = {"is_valid": True}
            rate_limits = _extract_rate_limits(result["headers"])
            if rate_limits["rpm"] is not None:
                check_result["rate_limit_rpm"] = rate_limits["rpm"]
            if rate_limits["tpm"] is not None:
                check_result["rate_limit_tpm"] = rate_limits["tpm"]
            if rate_limits["rpd"] is not None:
                check_result["rate_limit_rpd"] = rate_limits["rpd"]

            if provider_id == "openrouter":
                credits = _extract_openrouter_credits(result["headers"])
                if credits["remaining"] is not None:
                    check_result["credits_remaining"] = credits["remaining"]
                if credits["used"] is not None:
                    check_result["credits_used"] = credits["used"]
                if credits["total"] is not None:
                    check_result["credits_total"] = credits["total"]

            if result["body"] is not None:
                models = _parse_models_from_body(provider, result["body"])
                if models:
                    check_result["models"] = models

            credit_endpoint = provider.get("credit_check_endpoint")
            if credit_endpoint:
                try:
                    credit_base = _get_base_url(provider, metadata)
                    if provider_id == "deepseek":
                        credit_base = re.sub(r"/v1/?$", "", credit_base)
                    credit_url = f"{credit_base}{credit_endpoint}"
                    credit_result = await _fetch_with_headers(credit_url, headers=headers)
                    if credit_result["status"] == 200 and credit_result["body"] is not None:
                        _parse_credit_check_response(
                            provider_id,
                            credit_result["body"],
                            check_result,
                        )
                    elif provider_id == "openrouter" and credit_result["status"] in {401, 403}:
                        return {
                            "is_valid": False,
                            "error": f"Auth failed ({credit_result['status']})",
                        }
                    else:
                        logger.warning(
                            "%s credit check returned HTTP %s; credits left unknown.",
                            provider_id,
                            credit_result["status"],
                        )
                except BlockedUrlError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "%s credit check failed: %s; credits left unknown.",
                        provider_id,
                        exc,
                    )

            compute_health_status(check_result)

            if USABILITY_PROBE_ENABLED and not skip_probe:
                await _probe_usability(key_value, provider, metadata, check_result)
            else:
                check_result["is_usable"] = check_result["is_valid"]
                check_result["usability_status"] = "unknown"

            return check_result

        if result["status"] in {401, 403}:
            return {"is_valid": False, "error": f"Auth failed ({result['status']})"}
        if result["status"] == 429:
            limits = _extract_rate_limits(result["headers"])
            partial: dict[str, Any] = {
                "is_valid": True,
                "partial_check": True,
                "error": "Rate limited during check",
            }
            if limits["rpm"] is not None:
                partial["rate_limit_rpm"] = limits["rpm"]
            if limits["tpm"] is not None:
                partial["rate_limit_tpm"] = limits["tpm"]
            return partial
        return {"is_valid": False, "error": f"HTTP {result['status']}"}
    except BlockedUrlError as exc:
        return {"is_valid": False, "error": f"Blocked endpoint: {exc}"}
    except TimeoutError:
        return {"is_valid": False, "error": "Connection timeout"}
    except Exception as exc:
        return {"is_valid": False, "error": f"Connection error: {exc}"}


def _build_metadata(key: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if key.get("metadata"):
        try:
            parsed = json.loads(key["metadata"])
            if isinstance(parsed, dict):
                metadata.update(parsed)
        except json.JSONDecodeError:
            pass
    if key.get("custom_base_url"):
        metadata["custom_base_url"] = key["custom_base_url"]
    return metadata


def _resolve_status(key: dict[str, Any], is_valid: bool) -> str:
    if not is_valid:
        return "invalid"
    if (
        key.get("is_daily_limited")
        and key.get("daily_credit_limit") is not None
        and key.get("daily_credit_used") is not None
        and float(key["daily_credit_used"]) >= float(key["daily_credit_limit"])
    ):
        return "daily_exhausted"
    return "active"


def _enrich_models(
    models: list[dict[str, Any]],
    provider_id: str,
) -> list[dict[str, Any]]:
    enriched_models: list[dict[str, Any]] = []
    for model in models:
        enriched = enrich_model_with_catalog(model["model_id"], provider_id)
        item = dict(model)
        if enriched:
            item["display_name"] = enriched.get("display_name") or item.get("display_name")
            item["context_window"] = enriched.get("context_window") or item.get("context_window")
            item["max_output_tokens"] = enriched.get("max_output_tokens") or item.get(
                "max_output_tokens"
            )
            if enriched.get("pricing_input") is not None:
                item["pricing_input"] = enriched["pricing_input"]
            if enriched.get("pricing_output") is not None:
                item["pricing_output"] = enriched["pricing_output"]
            if enriched.get("pricing_cached_input") is not None:
                item["pricing_cached_input"] = enriched["pricing_cached_input"]
            if enriched.get("capabilities"):
                item["capabilities"] = enriched["capabilities"]
            if enriched.get("benchmarks"):
                item["benchmarks"] = enriched["benchmarks"]
            if enriched.get("tokens_per_second") is not None:
                item["tokens_per_second"] = enriched["tokens_per_second"]
        enriched_models.append(item)
    return enriched_models


async def check_upstream_key(db_path: str | Path, key_id: str) -> None:
    key = await get_upstream_key(db_path, key_id)
    if key is None:
        return

    previous_status = key.get("status")
    metadata = _build_metadata(key)

    try:
        result = await validate_key(key["key_value"], key["provider_id"], metadata)
        final_status = _resolve_status(key, bool(result.get("is_valid")))

        if result.get("is_valid") and result.get("partial_check"):
            await update_upstream_key(
                db_path,
                key_id,
                {
                    "status": final_status,
                    "is_valid": 1,
                    "last_checked_at": _now(),
                    "last_error": result.get("error"),
                },
            )
        elif result.get("is_valid"):
            merged_meta = metadata
            if result.get("metadata"):
                merged_meta = {**metadata, **result["metadata"]}

            await update_upstream_key(
                db_path,
                key_id,
                {
                    "status": final_status,
                    "is_valid": 1,
                    "credits_remaining": result.get("credits_remaining"),
                    "credits_total": result.get("credits_total"),
                    "credits_used": result.get("credits_used"),
                    "rate_limit_rpm": result.get("rate_limit_rpm"),
                    "rate_limit_tpm": result.get("rate_limit_tpm"),
                    "rate_limit_rpd": result.get("rate_limit_rpd"),
                    "health_status": result.get("health_status", "healthy"),
                    "health_warnings": result.get("health_warnings"),
                    "is_usable": 1 if result.get("is_usable") else 0,
                    "usability_status": result.get("usability_status", "unknown"),
                    "usability_note": result.get("usability_note"),
                    "metadata": merged_meta if merged_meta else None,
                    "last_checked_at": _now(),
                    "last_error": None,
                },
            )

            models = result.get("models")
            if models:
                await replace_models_for_key(
                    db_path,
                    upstream_key_id=key_id,
                    provider_id=key["provider_id"],
                    models=_enrich_models(models, key["provider_id"]),
                )
        else:
            error = result.get("error") or "Unknown error"
            await update_upstream_key(
                db_path,
                key_id,
                {
                    "status": "invalid",
                    "is_valid": 0,
                    "is_usable": 0,
                    "usability_status": "auth_failed",
                    "usability_note": error,
                    "last_checked_at": _now(),
                    "last_error": error,
                },
            )

        await record_upstream_key_history(
            db_path,
            upstream_key_id=key_id,
            previous_status=previous_status,
            new_status=final_status,
            credits_remaining=result.get("credits_remaining"),
            notes=result.get("error"),
        )
    except Exception as exc:
        await update_upstream_key(
            db_path,
            key_id,
            {
                "status": "error",
                "is_valid": 0,
                "last_checked_at": _now(),
                "last_error": str(exc),
            },
        )


async def check_all_upstream_keys(db_path: str | Path) -> int:
    from janus.storage.upstream_keys import list_upstream_keys

    keys = await list_upstream_keys(db_path)
    eligible = [
        key
        for key in keys
        if key.get("status") != "revoked" and key.get("provider_id") != "unidentified"
    ]
    if not eligible:
        return 0

    semaphore = asyncio.Semaphore(max(1, CHECK_CONCURRENCY))

    async def worker(key_id: str) -> None:
        async with semaphore:
            try:
                await check_upstream_key(db_path, key_id)
            except Exception as exc:
                logger.error("check_all_upstream_keys failed for %s: %s", key_id, exc)

    await asyncio.gather(*(worker(key["id"]) for key in eligible))
    return len(eligible)
