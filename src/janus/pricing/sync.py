"""Live pricing sync — fetches LiteLLM's and OpenRouter's pricing tables and
merges them into the ``pricing_catalog`` DB table.

The builtin ~40-model table (``pricing/builtin.py``) is frozen at release time
and misses most of the long tail of models actually routed in production. This
module keeps a much broader catalog fresh via a periodic fetch (wired up by
the scheduler in a later task).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from janus.storage.pricing_catalog import replace_catalog
from janus.storage.settings import set_setting

from .models import ModelPricing

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

_MTOK = 1_000_000.0

_LITELLM_SKIP_KEY = "sample_spec"
_LITELLM_ALLOWED_MODES = {None, "chat", "responses", "completion"}


class PricingSyncError(Exception):
    """Raised when both pricing sources fail to fetch/parse."""


def _bare_suffix(key: str) -> str | None:
    if "/" in key:
        return key.rsplit("/", 1)[1]
    return None


def _add_with_dual_key(result: dict[str, ModelPricing], key: str, pricing: ModelPricing) -> None:
    result[key] = pricing
    bare = _bare_suffix(key)
    if bare is not None and bare not in result:
        result[bare] = pricing


def parse_litellm(data: dict[str, Any]) -> dict[str, ModelPricing]:
    """Parse the LiteLLM ``model_prices_and_context_window.json`` shape.

    Skips the non-model ``sample_spec`` key, entries whose ``mode`` isn't
    chat-ish (embeddings/image/audio/rerank models don't have per-token text
    pricing that's comparable to the rest of the catalog), entries with an
    unhashable ``mode`` value, and entries with negative cost fields.
    """
    result: dict[str, ModelPricing] = {}
    for key in sorted(data.keys()):
        if key == _LITELLM_SKIP_KEY:
            continue
        entry = data[key]
        if not isinstance(entry, dict):
            continue
        mode = entry.get("mode")
        try:
            mode_allowed = mode in _LITELLM_ALLOWED_MODES
        except TypeError:
            # Unhashable mode (list/dict) -- skip this entry, not the whole source.
            continue
        if not mode_allowed:
            continue
        input_cost = entry.get("input_cost_per_token")
        output_cost = entry.get("output_cost_per_token")
        if not isinstance(input_cost, (int, float)) and not isinstance(output_cost, (int, float)):
            continue
        if isinstance(input_cost, (int, float)) and input_cost < 0:
            continue
        if isinstance(output_cost, (int, float)) and output_cost < 0:
            continue
        input_per_mtok = float(input_cost) * _MTOK if isinstance(input_cost, (int, float)) else 0.0
        output_per_mtok = (
            float(output_cost) * _MTOK if isinstance(output_cost, (int, float)) else 0.0
        )
        cache_creation = entry.get("cache_creation_input_token_cost")
        cache_read = entry.get("cache_read_input_token_cost")
        cache_creation_per_mtok = (
            float(cache_creation) * _MTOK if isinstance(cache_creation, (int, float)) else 0.0
        )
        cache_read_per_mtok = (
            float(cache_read) * _MTOK if isinstance(cache_read, (int, float)) else 0.0
        )
        pricing = ModelPricing(
            input_per_mtok=input_per_mtok,
            output_per_mtok=output_per_mtok,
            cache_creation_per_mtok=cache_creation_per_mtok,
            cache_read_per_mtok=cache_read_per_mtok,
        )
        _add_with_dual_key(result, key, pricing)
    return result


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_openrouter(data: dict[str, Any]) -> dict[str, ModelPricing]:
    """Parse the OpenRouter ``/api/v1/models`` shape.

    Pricing fields are per-token decimal strings. Skips entries that fail to
    parse, are free variants (both prompt and completion cost zero), or have
    a negative prompt/completion cost.
    """
    result: dict[str, ModelPricing] = {}
    entries = data.get("data")
    if not isinstance(entries, list):
        return result
    for entry in sorted(
        (e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)),
        key=lambda e: e["id"],
    ):
        model_id = entry["id"]
        pricing_raw = entry.get("pricing")
        if not isinstance(pricing_raw, dict):
            continue
        prompt = _parse_float(pricing_raw.get("prompt"))
        completion = _parse_float(pricing_raw.get("completion"))
        if prompt is None or completion is None:
            continue
        if prompt < 0 or completion < 0:
            continue
        if prompt == 0.0 and completion == 0.0:
            continue
        cache_read = _parse_float(pricing_raw.get("input_cache_read")) or 0.0
        cache_write = _parse_float(pricing_raw.get("input_cache_write")) or 0.0
        pricing = ModelPricing(
            input_per_mtok=prompt * _MTOK,
            output_per_mtok=completion * _MTOK,
            cache_creation_per_mtok=cache_write * _MTOK,
            cache_read_per_mtok=cache_read * _MTOK,
        )
        _add_with_dual_key(result, model_id, pricing)
    return result


def merge_sources(
    litellm: dict[str, ModelPricing], openrouter: dict[str, ModelPricing]
) -> dict[str, ModelPricing]:
    """Merge litellm and openrouter pricing tables; litellm wins on collision."""
    merged: dict[str, ModelPricing] = dict(openrouter)
    merged.update(litellm)
    return merged


def _rows_for_catalog(
    litellm: dict[str, ModelPricing], openrouter: dict[str, ModelPricing]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, pricing in litellm.items():
        rows.append(_row(model, pricing, "litellm"))
    litellm_keys = set(litellm.keys())
    for model, pricing in openrouter.items():
        if model in litellm_keys:
            continue
        rows.append(_row(model, pricing, "openrouter"))
    return rows


def _row(model: str, pricing: ModelPricing, source: str) -> dict[str, Any]:
    return {
        "model": model,
        "input_per_mtok": pricing.input_per_mtok,
        "output_per_mtok": pricing.output_per_mtok,
        "cache_creation_per_mtok": pricing.cache_creation_per_mtok,
        "cache_read_per_mtok": pricing.cache_read_per_mtok,
        "source": source,
    }


async def _fetch_litellm(client: httpx.AsyncClient) -> dict[str, ModelPricing]:
    try:
        response = await client.get(LITELLM_URL)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        return parse_litellm(data)
    except Exception:
        return {}


async def _fetch_openrouter(client: httpx.AsyncClient) -> dict[str, ModelPricing]:
    try:
        response = await client.get(OPENROUTER_URL)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        return parse_openrouter(data)
    except Exception:
        return {}


async def fetch_and_sync(db_path: str | Path) -> int:
    """Fetch both pricing sources, merge, and replace the pricing_catalog table.

    Each source is fetched independently — one source failing (network error,
    bad JSON, no usable entries, etc.) does not abort the other. Only raises
    ``PricingSyncError`` if both sources come back empty, which guards against
    ever wiping out an existing catalog with nothing.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        litellm = await _fetch_litellm(client)
        openrouter = await _fetch_openrouter(client)

    if not litellm and not openrouter:
        raise PricingSyncError("Both LiteLLM and OpenRouter pricing fetches failed")

    rows = _rows_for_catalog(litellm, openrouter)
    count = await replace_catalog(db_path, rows)

    await set_setting(db_path, "pricing_last_sync_at", datetime.now(UTC).isoformat())
    await set_setting(db_path, "pricing_catalog_count", str(count))

    return count
