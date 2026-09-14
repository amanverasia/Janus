"""Response-size budgets for the dashboard state endpoints (#111).

Seeds a production-like fixture -- roughly 1,100 model catalog rows, 350
routing accounts, and a 4,096-row pricing catalog -- then asserts each heavy
section stays under raw and gzip-encoded size budgets and that oversized
JSON responses ship gzip-compressed. Budgets have headroom over the measured
fixture so unrelated churn does not flap CI, but a payload regression of the
kind that produced the original 806 KB Models state fails here.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from janus.storage.pricing_catalog import replace_catalog
from janus.storage.providers_db import create_provider
from janus.storage.upstream_keys import create_upstream_key
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

# Models and routing are paged server-side (a page of ~25 rows); these budgets
# are the backstop behind the dedicated pagination tests -- loose enough that
# legitimate page/field growth does not flap CI, tight enough that an
# un-pagination regression (~700 KB models / ~110 KB routing) fails here.
RAW_BUDGETS: dict[str, int] = {
    "models": 80_000,
    "routing": 100_000,
    "providers": 130_000,
    "pricing": 40_000,
}
GZIP_BUDGETS: dict[str, int] = {
    "models": 15_000,
    "routing": 12_000,
    "providers": 35_000,
    "pricing": 12_000,
}
MODEL_ROWS_PER_PROVIDER = 190
PROVIDER_PREFIXES = ("openai", "anthropic", "gemini", "groq", "mistral", "deepseek")
INVENTORY_KEY_COUNT = 350
PRICING_ROW_COUNT = 4096


async def _seed_real_size_data(app: FastAPI) -> None:
    db_path = app.state.db_path
    for prefix in PROVIDER_PREFIXES:
        await create_provider(
            db_path,
            {
                "id": f"prov-{prefix}",
                "catalog_id": prefix,
                "prefix": prefix,
                "api_type": "openai_compat",
                "base_url": f"https://api.{prefix}.example/v1",
                "api_key": "sk-config-key-not-real",
                "models": [f"fixture-model-{i:04d}" for i in range(MODEL_ROWS_PER_PROVIDER)],
            },
        )
    for index in range(INVENTORY_KEY_COUNT):
        prefix = PROVIDER_PREFIXES[index % len(PROVIDER_PREFIXES)]
        await create_upstream_key(
            db_path,
            provider_id=prefix,
            key_value=f"sk-inv-fixture-{index:04d}",
            key_label=f"fixture account {index}",
        )
    from janus.storage.database import get_connection

    async with get_connection(db_path) as db:
        await db.execute("UPDATE upstream_keys SET status = 'active', is_valid = 1, is_usable = 1")
        await db.commit()
    await replace_catalog(
        db_path,
        [
            {
                "model": f"fixture-catalog/model-{i:05d}",
                "input_per_mtok": 1.5,
                "output_per_mtok": 6.0,
                "cache_creation_per_mtok": 1.875,
                "cache_read_per_mtok": 0.15,
                "source": "litellm",
            }
            for i in range(PRICING_ROW_COUNT)
        ],
    )
    from janus.dashboard.reload import reload_providers

    await reload_providers(app)


async def _raw_request(
    app: FastAPI, path: str, *, accept_gzip: bool = True
) -> tuple[int, bytes, dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {DASHBOARD_TEST_API_KEY}",
        "Accept": "application/json",
        "Host": "test",
    }
    if accept_gzip:
        headers["Accept-Encoding"] = "gzip"
    body = bytearray()
    response_headers: dict[str, str] = {}
    start_sent = False

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal start_sent
        if message["type"] == "http.response.start":
            start_sent = True
            for key, value in message.get("headers") or []:
                response_headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        elif start_sent:
            body.extend(message.get("body", b""))

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "root_path": "",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    path, _, query = path.partition("?")
    scope["path"] = path
    scope["raw_path"] = path.encode()
    scope["query_string"] = query.encode()
    await app(scope, receive, send)
    return 200, bytes(body), response_headers


@pytest.fixture
async def sized_app(tmp_path: Path) -> FastAPI:
    from janus.app import create_app
    from janus.config.schema import JanusConfig, ServerSettings
    from janus.storage.database import init_db

    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    app = with_dashboard_auth(create_app(config=cfg))
    await init_db(app.state.db_path)
    await _seed_real_size_data(app)
    return app


@pytest.mark.parametrize("section", sorted(RAW_BUDGETS))
async def test_state_payload_size_budgets(sized_app: FastAPI, section: str) -> None:
    _status, raw, headers = await _raw_request(sized_app, f"/dashboard/api/v2/state/{section}")
    assert headers.get("content-encoding") == "gzip"
    decompressed = gzip.decompress(raw)
    decoded = json.loads(decompressed)
    assert decoded["section"] == section

    assert len(decompressed) <= RAW_BUDGETS[section], (
        f"{section} state is {len(decompressed)} bytes, budget {RAW_BUDGETS[section]}"
    )
    assert len(raw) <= GZIP_BUDGETS[section], (
        f"{section} gzip wire size is {len(raw)} bytes, budget {GZIP_BUDGETS[section]}"
    )


async def test_pricing_catalog_is_paginated_and_searchable(sized_app: FastAPI) -> None:
    _status, raw, _headers = await _raw_request(sized_app, "/dashboard/api/v2/state/pricing")
    payload = json.loads(gzip.decompress(raw))
    data = payload["data"]
    pagination = payload["meta"]["pagination"]
    assert len(data["catalog"]) == pagination["limit"]
    assert pagination["total"] == PRICING_ROW_COUNT
    assert pagination["total_pages"] > 1

    _status, raw, _headers = await _raw_request(
        sized_app,
        "/dashboard/api/v2/state/pricing?search=model-00042&limit=200&offset=0",
    )
    payload = json.loads(gzip.decompress(raw))
    assert payload["meta"]["pagination"]["total"] == 1
    assert payload["data"]["catalog"][0]["model"].endswith("model-00042")


async def test_models_state_is_paginated_and_searchable(sized_app: FastAPI) -> None:
    expected_total = MODEL_ROWS_PER_PROVIDER * len(PROVIDER_PREFIXES)
    _status, raw, _headers = await _raw_request(sized_app, "/dashboard/api/v2/state/models")
    payload = json.loads(gzip.decompress(raw))
    data = payload["data"]
    pagination = payload["meta"]["pagination"]
    assert len(data["models"]) == pagination["limit"]
    assert pagination["total"] == expected_total
    assert pagination["total_pages"] > 1
    # The provider list stays whole (it drives the provider filter rail).
    assert len(data["providers"]) == len(PROVIDER_PREFIXES)

    _status, raw, _headers = await _raw_request(
        sized_app, "/dashboard/api/v2/state/models?provider=openai&limit=200"
    )
    payload = json.loads(gzip.decompress(raw))
    assert payload["meta"]["pagination"]["total"] == MODEL_ROWS_PER_PROVIDER

    _status, raw, _headers = await _raw_request(
        sized_app, "/dashboard/api/v2/state/models?search=fixture-model-0001"
    )
    payload = json.loads(gzip.decompress(raw))
    narrowed = payload["meta"]["pagination"]["total"]
    assert 0 < narrowed < expected_total


async def test_routing_state_is_paginated_and_slim(sized_app: FastAPI) -> None:
    _status, raw, _headers = await _raw_request(sized_app, "/dashboard/api/v2/state/routing")
    payload = json.loads(gzip.decompress(raw))
    data = payload["data"]
    pagination = payload["meta"]["pagination"]
    assert len(data["accounts"]) <= pagination["limit"]
    assert 0 < pagination["total"] <= INVENTORY_KEY_COUNT
    assert pagination["total_pages"] > 1
    # The overview ships slim provider summaries -- per-account lists are
    # stripped (they used to be the whole 350-account payload) and the
    # aggregates the stat cards need travel as counts instead.
    for provider in data["overview"]["providers"]:
        assert provider["accounts"] == []
    assert data["overview"]["total_accounts"] == pagination["total"]
    assert isinstance(data["cooldowns"], list)


async def test_state_response_time_budgets(sized_app: FastAPI) -> None:
    """Warm response time for each heavy section stays bounded (#111).

    Generous bounds catch catastrophic regressions (e.g. re-materializing the
    full account pool) without flapping on runner variance; the byte-size
    budgets above are the deterministic gate for serialize/parse cost.
    """
    import time

    time_budget_ms = {"models": 1500, "routing": 1500, "providers": 1500, "pricing": 1500}
    for section in sorted(time_budget_ms):
        await _raw_request(sized_app, f"/dashboard/api/v2/state/{section}")  # warm
        start = time.perf_counter()
        _status, _raw, _headers = await _raw_request(
            sized_app, f"/dashboard/api/v2/state/{section}"
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < time_budget_ms[section], (
            f"{section} warm state response took {elapsed_ms:.0f}ms"
        )


async def test_state_responses_stay_uncompressed_for_non_gzip_clients(sized_app: FastAPI) -> None:
    _status, raw, headers = await _raw_request(
        sized_app, "/dashboard/api/v2/state/models", accept_gzip=False
    )
    assert "content-encoding" not in headers
    payload = json.loads(raw)
    assert payload["section"] == "models"


async def test_small_state_responses_are_not_compressed(sized_app: FastAPI) -> None:
    _status, raw, headers = await _raw_request(sized_app, "/dashboard/api/v2/state/tools")
    assert headers.get("content-type", "").startswith("application/json")
    assert "content-encoding" not in headers
    payload = json.loads(raw)
    assert payload["section"] == "tools"
