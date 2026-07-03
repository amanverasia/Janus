from __future__ import annotations

import os
from typing import Any

DEFAULT_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "CNY": 0.138,
    "CNH": 0.138,
}


def usd_rate_for(currency: str) -> float:
    code = currency.upper().strip()
    if not code:
        return 1.0
    env_specific = os.environ.get(f"INVENTORY_{code}_USD_RATE")
    if env_specific:
        return float(env_specific)
    if code in {"CNY", "CNH"}:
        return float(os.environ.get("INVENTORY_CNY_USD_RATE", str(DEFAULT_USD_RATES["CNY"])))
    return DEFAULT_USD_RATES.get(code, 1.0)


def convert_to_usd(amount: float, currency: str) -> float:
    return round(amount * usd_rate_for(currency), 2)


def normalize_credits_to_usd(
    remaining: float,
    total: float,
    used: float,
    currency: str,
) -> tuple[float, float, float, dict[str, Any]]:
    code = currency.upper().strip() or "USD"
    if code == "USD":
        return remaining, total, used, {}
    rate = usd_rate_for(code)
    meta: dict[str, Any] = {
        "credits_currency": code,
        "credits_original_remaining": remaining,
        "credits_original_total": total,
        "credits_original_used": used,
        "credits_usd_rate": rate,
    }
    return (
        convert_to_usd(remaining, code),
        convert_to_usd(total, code),
        convert_to_usd(used, code),
        meta,
    )
