"""CCH billing-header signing for Claude OAuth upstream requests."""

from __future__ import annotations

import json
import re
from typing import Any

_CCH_SEED = 0x6E52736AC806831E
_CCH_PATTERN = re.compile(r"\bcch=([0-9a-f]{5});", re.I)

_PRIME64_1 = 0x9E3779B185EBCA87
_PRIME64_2 = 0xC2B2AE3D27D4EB4F
_PRIME64_3 = 0x165667B19E3779F9
_PRIME64_4 = 0x85EBCA77C2B2AE63
_PRIME64_5 = 0x27D4EB2F165667C5


def _rotl64(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & 0xFFFFFFFFFFFFFFFF


def _xxhash64(data: bytes, seed: int = 0) -> int:
    length = len(data)
    offset = 0
    h64 = (seed + _PRIME64_5) & 0xFFFFFFFFFFFFFFFF

    if length >= 32:
        limit = length - 32
        v1 = (seed + _PRIME64_1 + _PRIME64_2) & 0xFFFFFFFFFFFFFFFF
        v2 = (seed + _PRIME64_2) & 0xFFFFFFFFFFFFFFFF
        v3 = seed & 0xFFFFFFFFFFFFFFFF
        v4 = (seed - _PRIME64_1) & 0xFFFFFFFFFFFFFFFF
        while offset <= limit:
            lane = int.from_bytes(data[offset : offset + 8], "little")
            v1 = (_rotl64((v1 + lane * _PRIME64_2) & 0xFFFFFFFFFFFFFFFF, 31) * _PRIME64_1) & (
                0xFFFFFFFFFFFFFFFF
            )
            lane = int.from_bytes(data[offset + 8 : offset + 16], "little")
            v2 = (_rotl64((v2 + lane * _PRIME64_2) & 0xFFFFFFFFFFFFFFFF, 31) * _PRIME64_1) & (
                0xFFFFFFFFFFFFFFFF
            )
            lane = int.from_bytes(data[offset + 16 : offset + 24], "little")
            v3 = (_rotl64((v3 + lane * _PRIME64_2) & 0xFFFFFFFFFFFFFFFF, 31) * _PRIME64_1) & (
                0xFFFFFFFFFFFFFFFF
            )
            lane = int.from_bytes(data[offset + 24 : offset + 32], "little")
            v4 = (_rotl64((v4 + lane * _PRIME64_2) & 0xFFFFFFFFFFFFFFFF, 31) * _PRIME64_1) & (
                0xFFFFFFFFFFFFFFFF
            )
            offset += 32
        h64 = (
            _rotl64(v1, 1) + _rotl64(v2, 7) + _rotl64(v3, 12) + _rotl64(v4, 18)
        ) & 0xFFFFFFFFFFFFFFFF
        for lane_val in (v1, v2, v3, v4):
            h64 = (h64 ^ _rotl64(lane_val * _PRIME64_2, 31) * _PRIME64_1) & 0xFFFFFFFFFFFFFFFF
            h64 = (h64 * _PRIME64_1 + _PRIME64_4) & 0xFFFFFFFFFFFFFFFF

    while offset + 8 <= length:
        lane = int.from_bytes(data[offset : offset + 8], "little")
        h64 = (h64 ^ _rotl64(lane * _PRIME64_2, 31) * _PRIME64_1) & 0xFFFFFFFFFFFFFFFF
        h64 = (h64 * _PRIME64_1 + _PRIME64_4) & 0xFFFFFFFFFFFFFFFF
        offset += 8

    if offset + 4 <= length:
        lane = int.from_bytes(data[offset : offset + 4], "little")
        h64 = (h64 ^ (lane * _PRIME64_1)) & 0xFFFFFFFFFFFFFFFF
        h64 = (_rotl64(h64, 23) * _PRIME64_2 + _PRIME64_3) & 0xFFFFFFFFFFFFFFFF
        offset += 4

    while offset < length:
        h64 = (h64 ^ (data[offset] * _PRIME64_5)) & 0xFFFFFFFFFFFFFFFF
        h64 = (_rotl64(h64, 11) * _PRIME64_1) & 0xFFFFFFFFFFFFFFFF
        offset += 1

    h64 ^= length
    h64 ^= h64 >> 33
    h64 = (h64 * _PRIME64_2) & 0xFFFFFFFFFFFFFFFF
    h64 ^= h64 >> 29
    h64 = (h64 * _PRIME64_3) & 0xFFFFFFFFFFFFFFFF
    h64 ^= h64 >> 32
    return h64


def sign_anthropic_messages_body(body: dict[str, Any]) -> dict[str, Any]:
    """Recompute CCH hash in the billing system block for OAuth upstream."""
    system = body.get("system")
    if not isinstance(system, list) or not system:
        return body
    first = system[0]
    if not isinstance(first, dict):
        return body
    text = first.get("text")
    if not isinstance(text, str) or not text.startswith("x-anthropic-billing-header:"):
        return body
    if not _CCH_PATTERN.search(text):
        return body

    unsigned_text = _CCH_PATTERN.sub("cch=00000;", text, count=1)
    unsigned = dict(body)
    unsigned_system = [dict(first, text=unsigned_text), *system[1:]]
    unsigned["system"] = unsigned_system
    unsigned_bytes = json.dumps(unsigned, separators=(",", ":"), ensure_ascii=False).encode()
    cch = f"{_xxhash64(unsigned_bytes, _CCH_SEED) & 0xFFFFF:05x}"
    signed_text = _CCH_PATTERN.sub(f"cch={cch};", unsigned_text, count=1)
    signed = dict(body)
    signed_system = [dict(first, text=signed_text), *system[1:]]
    signed["system"] = signed_system
    return signed
