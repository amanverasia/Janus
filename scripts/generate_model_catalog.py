#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    REPO_ROOT.parent
    / "Dashboard_For_Apis"
    / "backend"
    / "src"
    / "services"
    / "model-catalog.ts"
)
OUTPUT_JSON = REPO_ROOT / "src" / "janus" / "inventory" / "data" / "model_catalog.json"
OUTPUT_PY_HEADER = """from __future__ import annotations

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
"""


def _extract_array_text(source: str) -> str:
    marker = "export const MODEL_CATALOG"
    start = source.index(marker)
    assign = source.index("=", start)
    bracket = source.index("[", assign)
    depth = 0
    for index, char in enumerate(source[bracket:], start=bracket):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[bracket : index + 1]
    raise ValueError("Could not find MODEL_CATALOG array end")


def _ts_array_to_json(text: str) -> str:
    without_comments = re.sub(r"//[^\n]*", "", text)
    without_comments = re.sub(r"/\*.*?\*/", "", without_comments, flags=re.DOTALL)
    normalized = re.sub(r"(?<=\d)_(?=\d)", "", without_comments)
    normalized = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', normalized)
    normalized = re.sub(r"(\s)([a-zA-Z_][a-zA-Z0-9_]*)(\s*):", r'\1"\2"\3:', normalized)
    normalized = re.sub(r",(\s*[}\]])", r"\1", normalized)
    return normalized


def main() -> None:
    if not SOURCE.is_file():
        print(f"Source not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)

    source_text = SOURCE.read_text()
    array_text = _extract_array_text(source_text)
    json_text = _ts_array_to_json(array_text)
    catalog = json.loads(json_text)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(catalog, indent=2) + "\n")

    model_catalog_py = REPO_ROOT / "src" / "janus" / "inventory" / "model_catalog.py"
    model_catalog_py.write_text(OUTPUT_PY_HEADER)

    print(f"Wrote {len(catalog)} entries to {OUTPUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
