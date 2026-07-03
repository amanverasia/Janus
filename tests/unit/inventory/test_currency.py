from __future__ import annotations

import pytest

from janus.inventory.currency import convert_to_usd, normalize_credits_to_usd, usd_rate_for


def test_usd_rate_defaults() -> None:
    assert usd_rate_for("USD") == 1.0
    assert usd_rate_for("CNY") == pytest.approx(0.138)


def test_usd_rate_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVENTORY_CNY_USD_RATE", "0.2")
    assert usd_rate_for("CNY") == 0.2


def test_convert_to_usd_cny() -> None:
    assert convert_to_usd(1000.0, "CNY") == 138.0


def test_normalize_credits_to_usd_keeps_usd() -> None:
    remaining, total, used, meta = normalize_credits_to_usd(10.0, 20.0, 10.0, "USD")
    assert remaining == 10.0
    assert total == 20.0
    assert used == 10.0
    assert meta == {}


def test_normalize_credits_to_usd_converts_cny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVENTORY_CNY_USD_RATE", "0.1")
    remaining, total, used, meta = normalize_credits_to_usd(9558.21, 10000.0, 441.79, "CNY")
    assert remaining == pytest.approx(955.82)
    assert total == 1000.0
    assert used == pytest.approx(44.18)
    assert meta["credits_currency"] == "CNY"
    assert meta["credits_original_remaining"] == 9558.21
