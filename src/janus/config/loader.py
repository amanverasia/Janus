from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .schema import JanusConfig

_VAR_RE = re.compile(r"\$\{(?P<var>[A-Z][A-Z0-9_]*)\}", re.ASCII)


def resolve_vars(
    value: Any, env: Mapping[str, str] | None = None
) -> Any:
    if env is None:
        env = os.environ
    if isinstance(value, str):
        return _VAR_RE.sub(lambda m: env.get(m.group("var"), ""), value)
    if isinstance(value, dict):
        return {k: resolve_vars(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_vars(v, env) for v in value]
    return value


def load_config(path: str | Path) -> JanusConfig:
    path = Path(path).expanduser()
    if not path.exists():
        return JanusConfig()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    resolved = resolve_vars(raw)
    if isinstance(resolved, dict):
        resolved = {k: v for k, v in resolved.items() if v is not None}
    return JanusConfig(**(resolved if isinstance(resolved, dict) else {}))
