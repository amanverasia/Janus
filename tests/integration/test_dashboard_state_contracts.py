"""Typed dashboard state contracts (#110), two tiers.

Stable sections are byte-pinned: fetched from a deterministic seeded app and
compared against a committed fixture under ``dashboard-ui/src/lib/contract-fixtures/``,
which the Svelte side type-checks against the ``$lib/contracts`` interfaces via
``contracts-check.ts`` during ``scripts/build_dashboard_ui.py --check``.

Volatile sections -- the ones actively reshaped by payload-size work (pricing
pagination #162, state dedup #160, model capabilities #152) -- are pinned by a
generated key-path/type signature (``<section>.shape.json``) instead. The
signature records every object key path in the tree and its JSON type, so a
renamed, added, removed, or retyped field anywhere still fails this test, but
value churn and list-length changes do not.

Dates and timestamps are normalized to fixed tokens so fixtures stay stable
across days. Rebuild fixtures after an intentional contract change with:

    JANUS_REGEN_CONTRACT_FIXTURES=1 .venv/bin/python -m pytest \
        tests/integration/test_dashboard_state_contracts.py
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from janus.dashboard.api_v2 import _SECTIONS
from tests.fixtures.dashboard_auth import DASHBOARD_TEST_API_KEY, with_dashboard_auth

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "dashboard-ui" / "src" / "lib" / "contract-fixtures"

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?Z?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_JANUS_KEY_PREFIX_RE = re.compile(r"sk-janus-[0-9a-f]+")
_RETRY_AFTER_RE = re.compile(r'"retry_after": \d+(\.\d+)?')

_ENVELOPE_KEYS = {"section", "alerts", "data", "meta"}

# Full-payload fixtures: small sections whose shape only changes on purpose.
FIXTURE_SECTIONS = frozenset(
    {
        "analytics",
        "budgets",
        "combos",
        "keys",
        "leaderboard",
        "overview",
        "request-logs",
        "savers",
        "settings",
        "tools",
        "usage",
    }
)
# Key-path/type signatures: heavy sections tuned by payload-size work.
SHAPE_SECTIONS = frozenset(
    {
        "inventory",
        "inventory-keys",
        "models",
        "pricing",
        "providers",
        "routing",
    }
)
assert FIXTURE_SECTIONS | SHAPE_SECTIONS == set(_SECTIONS), (
    "Every state section must be classified as fixture- or shape-pinned; "
    f"unclassified: {sorted(set(_SECTIONS) - FIXTURE_SECTIONS - SHAPE_SECTIONS)}"
)

_SIGNATURE_LIST_SAMPLE = 20


def _normalize(section: str, payload: dict[str, Any]) -> str:
    if section == "overview":
        live = payload["data"].get("live")
        if isinstance(live, dict):
            live["seq"] = 0
            live["recent"] = []
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    text = _TIMESTAMP_RE.sub("2000-01-01T00:00:00Z", text)
    text = _DATE_RE.sub("2000-01-01", text)
    text = _UUID_RE.sub("00000000-0000-0000-0000-000000000000", text)
    text = _JANUS_KEY_PREFIX_RE.sub("sk-janus-fixed0", text)
    text = _RETRY_AFTER_RE.sub('"retry_after": 0', text)
    return text + "\n"


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _signature(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        entries: list[str] = []
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            entries.append(f"{path} {_type_name(value[key])}")
            entries.extend(_signature(value[key], path))
        return entries
    if isinstance(value, list):
        merged: set[str] = set()
        for item in value[:_SIGNATURE_LIST_SAMPLE]:
            merged.update(_signature(item, f"{prefix}[]"))
        return sorted(merged)
    return []


def _validate_envelope(section: str, payload: dict[str, Any]) -> None:
    assert set(payload) == _ENVELOPE_KEYS, f"[{section}] envelope keys drifted: {set(payload)}"
    assert payload["section"] == section
    assert isinstance(payload["alerts"], list)
    for alert in payload["alerts"]:
        assert {"id", "severity", "title", "detail"} <= set(alert), f"[{section}] alert shape"


async def _seed_contract_app(app: FastAPI) -> None:
    from janus.storage.api_keys import create_key
    from janus.storage.budgets import create_or_update_budget
    from janus.storage.combos_db import create_combo
    from janus.storage.database import init_db
    from janus.storage.pricing_catalog import replace_catalog
    from janus.storage.pricing_db import create_or_update_pricing_override
    from janus.storage.providers_db import create_provider
    from janus.storage.request_logs import record_request_log
    from janus.storage.upstream_keys import create_upstream_key
    from janus.storage.usage import record_usage

    db_path = app.state.db_path
    await init_db(db_path)

    for prefix in ("openai", "anthropic"):
        await create_provider(
            db_path,
            {
                "id": f"prov-{prefix}",
                "catalog_id": prefix,
                "prefix": prefix,
                "api_type": "openai_compat",
                "base_url": f"https://api.{prefix}.example/v1",
                "api_key": "sk-config-key-not-real",
                "models": [f"fixture-model-{i:03d}" for i in range(12)],
            },
        )
    for index in range(4):
        await create_upstream_key(
            db_path,
            provider_id="openai" if index % 2 == 0 else "anthropic",
            key_value=f"sk-inv-fixture-{index:03d}",
            key_label=f"fixture account {index}",
        )
    await replace_catalog(
        db_path,
        [
            {
                "model": f"fixture-catalog/model-{i:03d}",
                "input_per_mtok": 1.5,
                "output_per_mtok": 6.0,
                "cache_creation_per_mtok": 1.875,
                "cache_read_per_mtok": 0.15,
                "source": "litellm",
            }
            for i in range(30)
        ],
    )
    await create_or_update_pricing_override(
        db_path,
        {
            "model": "fixture-catalog/model-000",
            "input_per_mtok": 2.0,
            "output_per_mtok": 8.0,
            "cache_creation_per_mtok": 2.5,
            "cache_read_per_mtok": 0.2,
        },
    )
    await record_usage(
        db_path,
        provider_id="prov-openai",
        model="openai/fixture-model-001",
        input_tokens=1200,
        output_tokens=340,
        status=200,
        cost=0.42,
    )
    await record_usage(
        db_path,
        provider_id="prov-anthropic",
        model="anthropic/fixture-model-002",
        input_tokens=800,
        output_tokens=110,
        status=200,
        cost=0.31,
    )
    await record_usage(
        db_path,
        provider_id="prov-openai",
        model="openai/fixture-model-001",
        input_tokens=100,
        output_tokens=10,
        status=500,
        cost=0.0,
    )
    await create_key(db_path, "fixture dashboard key")
    await create_or_update_budget(db_path, key_id=1, daily_limit=5.0, absolute_limit=25.0)
    await create_combo(
        db_path,
        {
            "name": "fixture-combo",
            "models": ["openai/fixture-model-001", "anthropic/fixture-model-002"],
        },
    )
    await record_request_log(
        db_path,
        client_format="openai",
        model="openai/fixture-model-001",
        provider_id="prov-openai",
        account_id="account-1",
        status=200,
        duration_ms=412,
        client_key_id=1,
        client_key_label="fixture dashboard key",
    )
    await record_request_log(
        db_path,
        client_format="openai",
        model="anthropic/fixture-model-002",
        provider_id="prov-anthropic",
        status=502,
        duration_ms=88,
        error="upstream connect error",
    )
    from janus.dashboard.reload import reload_providers

    await reload_providers(app)


@pytest.fixture
async def contract_app(tmp_path: Path) -> FastAPI:
    from janus.app import create_app
    from janus.config.schema import JanusConfig, ServerSettings

    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    app = with_dashboard_auth(create_app(config=cfg))
    await _seed_contract_app(app)
    return app


async def _fetch_section(app: FastAPI, section: str) -> dict[str, Any]:
    body = bytearray()
    headers_list: list[tuple[bytes, bytes]] = [
        (b"authorization", f"Bearer {DASHBOARD_TEST_API_KEY}".encode()),
        (b"accept", b"application/json"),
        (b"host", b"test"),
    ]

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/dashboard/api/v2/state/{section}",
        "raw_path": f"/dashboard/api/v2/state/{section}".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers_list,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    payload: dict[str, Any] = json.loads(bytes(body))
    return payload


def _assert_fixture(section: str, payload: dict[str, Any]) -> None:
    normalized_text = _normalize(section, payload)
    fixture_path = FIXTURE_DIR / f"{section}.json"
    if os.environ.get("JANUS_REGEN_CONTRACT_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(normalized_text, encoding="utf-8")
        return
    assert fixture_path.is_file(), (
        f"Missing contract fixture {fixture_path}; regenerate with "
        "JANUS_REGEN_CONTRACT_FIXTURES=1 pytest tests/integration/test_dashboard_state_contracts.py"
    )
    committed = json.loads(fixture_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_text)
    assert normalized == committed, (
        f"Contract fixture drift for section '{section}'. If the backend change is "
        "intentional, regenerate fixtures (JANUS_REGEN_CONTRACT_FIXTURES=1) and update "
        "dashboard-ui/src/lib/contracts.ts in the same change."
    )


def _assert_signature(section: str, payload: dict[str, Any]) -> None:
    signature = _signature(payload)
    fixture_path = FIXTURE_DIR / f"{section}.shape.json"
    if os.environ.get("JANUS_REGEN_CONTRACT_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(json.dumps(signature, indent=2) + "\n", encoding="utf-8")
        return
    assert fixture_path.is_file(), (
        f"Missing shape signature {fixture_path}; regenerate with "
        "JANUS_REGEN_CONTRACT_FIXTURES=1 pytest tests/integration/test_dashboard_state_contracts.py"
    )
    committed = json.loads(fixture_path.read_text(encoding="utf-8"))
    added = sorted(set(signature) - set(committed))
    removed = sorted(set(committed) - set(signature))
    assert not added and not removed, (
        f"State contract drift for section '{section}'. New fields: {added}. "
        f"Removed fields: {removed}. If the backend change is intentional, regenerate "
        "signatures (JANUS_REGEN_CONTRACT_FIXTURES=1) in the same change."
    )


@pytest.mark.parametrize("section", sorted(_SECTIONS))
async def test_state_section_contract(contract_app: FastAPI, section: str) -> None:
    payload = await _fetch_section(contract_app, section)
    _validate_envelope(section, payload)
    if section in FIXTURE_SECTIONS:
        _assert_fixture(section, payload)
    else:
        _assert_signature(section, payload)
