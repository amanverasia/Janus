from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from janus.canonical.models import CanonicalRequest, Usage

CACHE_VERSION = 1

PROMPT_CACHE_PROVIDER_ID = "prompt-cache"


@dataclass
class CachedResponse:
    payload: dict[str, Any]
    model: str
    provider_id: str
    account_id: str | None
    usage: Usage
    created: float = field(default=0.0)


def is_cacheable_request(req: CanonicalRequest) -> bool:
    if req.stream:
        return False
    if req.seed is not None:
        return True
    if req.temperature is None or req.temperature != 0:
        return False
    return req.top_p is None or req.top_p >= 1.0


def compute_cache_key(
    *,
    client_key_id: int | None,
    client_format: str,
    canonical_req: CanonicalRequest,
    thinking_intent: dict[str, Any] | None = None,
    client_tool: str | None = None,
) -> str:
    payload = {
        "v": CACHE_VERSION,
        "client_key_id": client_key_id,
        "client_format": client_format,
        "client_tool": client_tool or "",
        "thinking_intent": thinking_intent or "",
        "request": json.loads(canonical_req.model_dump_json()),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class PromptCache:
    def __init__(self) -> None:
        self._entries: OrderedDict[str, CachedResponse] = OrderedDict()

    def get(self, key: str, *, ttl_s: float) -> CachedResponse | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.created > ttl_s:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, key: str, entry: CachedResponse, *, max_entries: int) -> None:
        if max_entries <= 0:
            return
        if entry.created <= 0.0:
            entry.created = time.monotonic()
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


_cache = PromptCache()


def get_prompt_cache() -> PromptCache:
    return _cache


def prompt_cache_get(key: str, *, ttl_s: float) -> CachedResponse | None:
    return _cache.get(key, ttl_s=ttl_s)


def prompt_cache_put(
    key: str,
    entry: CachedResponse,
    *,
    max_entries: int,
    max_body_bytes: int,
) -> None:
    body_size = len(json.dumps(entry.payload, ensure_ascii=False, separators=(",", ":")).encode())
    if body_size > max_body_bytes:
        return
    _cache.put(key, entry, max_entries=max_entries)


def prompt_cache_clear() -> None:
    _cache.clear()
