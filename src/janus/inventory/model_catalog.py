from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_FILE = Path(__file__).with_name("data") / "model_catalog.json"


@lru_cache(maxsize=1)
def _load_catalog() -> list[dict[str, Any]]:
    raw = _DATA_FILE.read_text()
    data: list[dict[str, Any]] = json.loads(raw)
    return data


def get_model_catalog() -> list[dict[str, Any]]:
    return _load_catalog()


def enrich_model_with_catalog(model_id: str, provider_id: str) -> dict[str, Any] | None:
    for entry in _load_catalog():
        if entry["model_id"] == model_id and entry["provider_id"] == provider_id:
            return dict(entry)
    return None
