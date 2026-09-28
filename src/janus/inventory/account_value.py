from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from httpx import HTTPError

from janus.inventory.currency import normalize_credits_to_usd
from janus.inventory.url_guard import BlockedUrlError, safe_fetch

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 8.0
REFRESH_TTL_SECONDS = 600.0

_ZAI_INTL_ORIGIN = "https://api.z.ai"
_ZAI_CN_ORIGIN = "https://open.bigmodel.cn"
_ZAI_QUOTA_PATH = "/api/monitor/usage/quota/limit"
_ZAI_INTL_QUOTA_ORIGIN = f"{_ZAI_INTL_ORIGIN}/api/monitor"
_MINIMAX_REMAINS_PATH = "/v1/api/openplatform/coding_plan/remains"


class AccountValueStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class UsageWindow:
    label: str
    used_percent: float | None
    reset_at: str | None = None

    def __post_init__(self) -> None:
        if self.used_percent is not None:
            object.__setattr__(self, "used_percent", min(100.0, max(0.0, float(self.used_percent))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "used_percent": self.used_percent,
            "reset_at": self.reset_at,
        }


@dataclass(frozen=True)
class CreditValue:
    remaining: float | None
    total: float | None = None
    used: float | None = None
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "remaining": self.remaining,
            "total": self.total,
            "used": self.used,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class AccountValue:
    status: AccountValueStatus
    source: str
    fetched_at: str
    credits: CreditValue | None = None
    windows: list[UsageWindow] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "source": self.source,
            "fetched_at": self.fetched_at,
            "credits": self.credits.to_dict() if self.credits else None,
            "windows": [window.to_dict() for window in self.windows],
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountValue:
        credits_raw = data.get("credits")
        windows_raw = data.get("windows")
        metadata_raw = data.get("metadata")
        if not isinstance(metadata_raw, dict):
            metadata_raw = {}
        try:
            status = AccountValueStatus(str(data.get("status") or AccountValueStatus.OK))
        except ValueError:
            status = AccountValueStatus.OK
        return cls(
            status=status,
            source=str(data.get("source") or ""),
            fetched_at=str(data.get("fetched_at") or ""),
            credits=CreditValue(**credits_raw) if isinstance(credits_raw, dict) else None,
            windows=[
                UsageWindow(
                    label=str(window.get("label") or ""),
                    used_percent=_finite(window.get("used_percent")),
                    reset_at=_optional_str(window.get("reset_at")),
                )
                for window in windows_raw
                if isinstance(window, dict)
            ]
            if isinstance(windows_raw, list)
            else [],
            error=data.get("error") if isinstance(data.get("error"), str) else None,
            metadata=metadata_raw,
        )


class ProbeError(Exception):
    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.transient = transient


def worst_window_percent(value: AccountValue) -> float | None:
    percents = [window.used_percent for window in value.windows if window.used_percent is not None]
    return max(percents) if percents else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _percent(value: Any) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    return min(100.0, max(0.0, number))


def _reset_at(value: Any) -> str | None:
    number = _finite(value)
    if number is None or number <= 0:
        return None
    if number > 1e10:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}"


def _effective_base_url(base_url: str, custom_base_url: str | None) -> str:
    if custom_base_url:
        return custom_base_url.rstrip("/")
    return (base_url or "").rstrip("/")


def _json_object(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProbeError("non-JSON quota response", transient=True) from exc
    return body if isinstance(body, dict) else {}


def _child(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _http_error(response_status: int) -> ProbeError:
    terminal = 400 <= response_status < 500 and response_status not in {408, 429}
    return ProbeError(
        f"HTTP {response_status} from quota endpoint",
        transient=not terminal,
    )


async def _fetch_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    try:
        response = await safe_fetch(url, headers=headers, timeout=PROBE_TIMEOUT_SECONDS)
    except BlockedUrlError:
        raise
    except HTTPError as exc:
        raise ProbeError(f"quota fetch failed: {exc}", transient=True) from exc
    if response.status_code != 200:
        raise _http_error(response.status_code)
    return _json_object(response)


def _probe_source(provider_id: str, endpoint: str) -> str:
    return f"{provider_id}:{endpoint}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _probe_openrouter(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or "https://openrouter.ai/api/v1"
    body = await _fetch_json(
        f"{base}/key",
        headers={"Authorization": f"Bearer {key_value}"},
    )
    data = _child(body, "data") or body
    total = _finite(data.get("limit"))
    remaining = _finite(data.get("limit_remaining"))
    usage = _finite(data.get("usage"))
    used: float | None = None
    if total is not None and remaining is not None:
        used = max(0.0, total - remaining)
    elif usage is not None:
        used = usage
    credits = CreditValue(remaining=remaining, total=total, used=used)
    metadata = {
        field: data[field]
        for field in ("is_free_tier", "usage_daily", "usage_weekly", "usage_monthly", "limit_reset")
        if data.get(field) is not None
    }
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("openrouter", "key"),
        fetched_at=_now_iso(),
        credits=credits,
        metadata=metadata,
    )


async def _probe_deepseek(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or "https://api.deepseek.com"
    stripped = base
    for suffix in ("/v1", "/beta"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
    body = await _fetch_json(
        f"{stripped}/user/balance",
        headers={"Authorization": f"Bearer {key_value}"},
    )
    infos = body.get("balance_infos")
    info: dict[str, Any] = {}
    if isinstance(infos, list):
        dicts = [item for item in infos if isinstance(item, dict)]
        usd = next((item for item in dicts if item.get("currency") == "USD"), None)
        info = usd or (dicts[0] if dicts else {})
    total_balance = _finite(info.get("total_balance")) or 0.0
    granted = _finite(info.get("granted_balance")) or 0.0
    topped = _finite(info.get("topped_up_balance")) or 0.0
    raw_total = granted + topped
    raw_used = max(0.0, raw_total - total_balance)
    currency = str(info.get("currency") or "USD")
    remaining, total, used, fx = normalize_credits_to_usd(
        total_balance, raw_total, raw_used, currency
    )
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("deepseek", "balance"),
        fetched_at=_now_iso(),
        credits=CreditValue(remaining=remaining, total=total, used=used),
        metadata=fx,
    )


async def _probe_moonshot(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or "https://api.moonshot.ai"
    for suffix in ("/v1",):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    is_cn = base.startswith("https://api.moonshot.cn")
    body = await _fetch_json(
        f"{base}/v1/users/me/balance",
        headers={"Authorization": f"Bearer {key_value}"},
    )
    data = _child(body, "data") or body
    if body.get("code") not in (None, 0):
        raise ProbeError(str(body.get("message") or "moonshot quota error"), transient=True)
    available = _finite(data.get("available_balance"))
    if available is None or available < 0:
        raise ProbeError("moonshot balance unavailable", transient=True)
    cash = _finite(data.get("cash_balance")) or 0.0
    voucher = _finite(data.get("voucher_balance")) or 0.0
    raw_total = cash + voucher
    raw_used = max(0.0, raw_total - available)
    currency = "CNY" if is_cn else "USD"
    remaining, total, used, fx = normalize_credits_to_usd(available, raw_total, raw_used, currency)
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("moonshot", "balance"),
        fetched_at=_now_iso(),
        credits=CreditValue(remaining=remaining, total=total, used=used),
        metadata={**fx, "cash_balance": cash, "voucher_balance": voucher},
    )


def _parse_zai_limits(data: dict[str, Any]) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    limits = data.get("limits")
    if not isinstance(limits, list):
        return windows
    for raw in limits:
        row = raw if isinstance(raw, dict) else {}
        if row.get("type") not in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            continue
        reset = _reset_at(row.get("nextResetTime"))
        percent = _percent(row.get("percentage"))
        if percent is None:
            used = _finite(row.get("currentValue"))
            total = _finite(row.get("usage"))
            if used is not None and total is not None and total > 0:
                percent = _percent(used / total * 100)
        if percent is None:
            continue
        unit = _finite(row.get("unit"))
        number = _finite(row.get("number"))
        if unit == 3 and number == 5:
            windows.append(UsageWindow(label="5h", used_percent=percent, reset_at=reset))
        elif unit == 6 and number == 1:
            windows.append(UsageWindow(label="weekly", used_percent=percent, reset_at=reset))
    return windows


def _parse_zai_legacy(data: dict[str, Any]) -> list[UsageWindow]:
    nested = _child(data, "quota")

    def percent_at(*keys: str) -> float | None:
        for key in keys:
            percent = _percent(data.get(key))
            if percent is not None:
                return percent
            percent = _percent(nested.get(key))
            if percent is not None:
                return percent
        return None

    windows: list[UsageWindow] = []
    five_hour = percent_at("fiveHourPercent", "fiveHourUsage", "fiveHourUsed")
    if five_hour is not None:
        windows.append(UsageWindow(label="5h", used_percent=five_hour))
    weekly = percent_at("weeklyPercent", "weeklyUsage", "weeklyUsed")
    if weekly is not None:
        windows.append(UsageWindow(label="weekly", used_percent=weekly))
    monthly = percent_at("monthlyPercent", "mcpPercent", "monthlyMCPUsage")
    if monthly is not None:
        windows.append(UsageWindow(label="monthly (incl. MCP)", used_percent=monthly))
    return windows


async def _fetch_zai_quota(origin: str, key_value: str) -> dict[str, Any]:
    authorization = key_value if origin == _ZAI_CN_ORIGIN else f"Bearer {key_value}"
    return await _fetch_json(
        f"{origin}{_ZAI_QUOTA_PATH}",
        headers={"Accept": "application/json", "Authorization": authorization},
    )


async def _probe_zhipu(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or _ZAI_CN_ORIGIN
    primary = _origin(base)
    alternate = _ZAI_INTL_ORIGIN if primary == _ZAI_CN_ORIGIN else _ZAI_CN_ORIGIN
    try:
        body = await _fetch_zai_quota(primary, key_value)
    except ProbeError as exc:
        if exc.transient:
            raise
        body = await _fetch_zai_quota(alternate, key_value)
    if body.get("success") is False:
        raise ProbeError(str(body.get("msg") or "z.ai quota error"), transient=True)
    data = _child(body, "data") or body
    windows = _parse_zai_limits(data)
    if not windows and isinstance(data.get("limits"), list):
        return AccountValue(
            status=AccountValueStatus.OK,
            source=_probe_source("zhipu", "quota-limit"),
            fetched_at=_now_iso(),
        )
    if not windows:
        windows = _parse_zai_legacy(data)
    if not windows:
        raise ProbeError("z.ai quota response had no recognized windows", transient=True)
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("zhipu", "quota-limit"),
        fetched_at=_now_iso(),
        windows=windows,
    )


async def _probe_minimax(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or "https://api.minimax.io"
    host = (
        "https://api.minimaxi.com"
        if base.startswith("https://api.minimaxi.com")
        else "https://api.minimax.io"
    )
    body = await _fetch_json(
        f"{host}{_MINIMAX_REMAINS_PATH}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key_value}"},
    )
    base_resp = _child(body, "base_resp")
    if base_resp.get("status_code") != 0:
        raise ProbeError(str(base_resp.get("status_msg") or "minimax quota error"), transient=True)
    rows = body.get("model_remains")
    general = (
        next(
            (row for row in rows if isinstance(row, dict) and row.get("model_name") == "general"),
            None,
        )
        if isinstance(rows, list)
        else None
    )
    if general is None:
        raise ProbeError("minimax quota response had no general model row", transient=True)
    windows: list[UsageWindow] = []
    interval_remaining = _finite(general.get("current_interval_remaining_percent"))
    if interval_remaining is not None:
        windows.append(
            UsageWindow(
                label="Coding Plan 5-hour",
                used_percent=_percent(100 - interval_remaining),
                reset_at=_reset_at(general.get("end_time")),
            )
        )
    if general.get("current_weekly_status") == 1:
        weekly_remaining = _finite(general.get("current_weekly_remaining_percent"))
        if weekly_remaining is not None:
            windows.append(
                UsageWindow(
                    label="Coding Plan weekly",
                    used_percent=_percent(100 - weekly_remaining),
                    reset_at=_reset_at(general.get("weekly_end_time")),
                )
            )
    if not windows:
        raise ProbeError("minimax quota response had no window data", transient=True)
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("minimax", "token-plan-remains"),
        fetched_at=_now_iso(),
        windows=windows,
    )


async def _probe_venice(
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    base = _effective_base_url(base_url, custom_base_url) or "https://api.venice.ai/api/v1"
    body = await _fetch_json(
        f"{base}/billing/balance",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key_value}"},
    )
    data = _child(body, "data") or body
    diem = _finite(data.get("balance"))
    usd = _finite(data.get("balance_usd"))
    if diem is None and usd is None:
        raise ProbeError("venice balance unavailable", transient=True)
    remaining = usd if usd is not None else diem
    windows: list[UsageWindow] = []
    allocated = _finite(data.get("diem_epoch_allocated"))
    epoch_used = _finite(data.get("diem_epoch_used"))
    if allocated is not None and allocated > 0 and epoch_used is not None:
        windows.append(
            UsageWindow(
                label="DIEM epoch",
                used_percent=_percent(epoch_used / allocated * 100),
            )
        )
    return AccountValue(
        status=AccountValueStatus.OK,
        source=_probe_source("venice", "billing-balance"),
        fetched_at=_now_iso(),
        credits=CreditValue(remaining=remaining, currency="USD"),
        windows=windows,
        metadata={"diem_balance": diem} if diem is not None else {},
    )


AccountValueProbe = Callable[..., Awaitable[AccountValue]]

ACCOUNT_VALUE_PROBES: dict[str, AccountValueProbe] = {
    "openrouter": _probe_openrouter,
    "deepseek": _probe_deepseek,
    "moonshot": _probe_moonshot,
    "zhipu": _probe_zhipu,
    "minimax": _probe_minimax,
    "venice": _probe_venice,
}


async def probe_account_value(
    provider_id: str,
    key_value: str,
    base_url: str,
    custom_base_url: str | None,
) -> AccountValue:
    probe = ACCOUNT_VALUE_PROBES.get(provider_id)
    if probe is None:
        return AccountValue(
            status=AccountValueStatus.UNSUPPORTED,
            source=provider_id,
            fetched_at=_now_iso(),
        )
    return await probe(key_value, base_url, custom_base_url)


_inflight: dict[str, asyncio.Task[AccountValue | None]] = {}


def _stored_state(key: dict[str, Any]) -> dict[str, Any]:
    raw = key.get("account_value")
    parsed: dict[str, Any] = {}
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str) and raw:
        try:
            candidate = json.loads(raw)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = {}
    return {
        "value": parsed,
        "status": str(key.get("account_value_status") or ""),
        "error": _optional_str(key.get("account_value_error")),
        "fetched_at": _optional_str(key.get("account_value_fetched_at")),
        "checked_at": _optional_str(key.get("account_value_checked_at")),
    }


def _state_fresh(state: dict[str, Any]) -> bool:
    checked_at = state.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at:
        return False
    try:
        checked = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    age = (datetime.now(UTC) - checked).total_seconds()
    return 0 <= age < REFRESH_TTL_SECONDS


async def refresh_account_value(
    db_path: str | Path,
    key_id: str,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    from janus.storage.upstream_keys import get_upstream_key

    key = await get_upstream_key(db_path, key_id)
    if key is None:
        return None
    state = _stored_state(key)
    if not force and state["status"] != AccountValueStatus.UNSUPPORTED and _state_fresh(state):
        return state

    existing_task = _inflight.get(key_id)
    if existing_task is not None:
        await existing_task
        refreshed = await get_upstream_key(db_path, key_id)
        return _stored_state(refreshed) if refreshed else None

    task = asyncio.create_task(
        _refresh_account_value_task(db_path, key_id, key, state),
    )
    _inflight[key_id] = task
    try:
        await task
    finally:
        _inflight.pop(key_id, None)
    refreshed = await get_upstream_key(db_path, key_id)
    return _stored_state(refreshed) if refreshed else None


async def _refresh_account_value_task(
    db_path: str | Path,
    key_id: str,
    key: dict[str, Any],
    state: dict[str, Any],
) -> AccountValue | None:
    from janus.storage.upstream_keys import update_upstream_key

    provider_id = str(key.get("provider_id") or "")
    probe = ACCOUNT_VALUE_PROBES.get(provider_id)
    if probe is None:
        if state.get("status") != AccountValueStatus.UNSUPPORTED:
            await update_upstream_key(
                db_path,
                key_id,
                {
                    "account_value_status": str(AccountValueStatus.UNSUPPORTED),
                    "account_value_error": None,
                    "account_value_checked_at": _now_iso(),
                },
            )
        return None

    from janus.inventory.catalog import get_inventory_provider

    provider = get_inventory_provider(provider_id) or {}
    base_url = str(provider.get("base_url") or "")
    try:
        value = await probe_account_value(
            provider_id,
            str(key.get("key_value") or ""),
            base_url,
            key.get("custom_base_url") if isinstance(key.get("custom_base_url"), str) else None,
        )
    except ProbeError as exc:
        await update_upstream_key(
            db_path,
            key_id,
            {
                "account_value_status": str(AccountValueStatus.UNAVAILABLE),
                "account_value_error": str(exc),
                "account_value_checked_at": _now_iso(),
            },
        )
        return None
    except Exception as exc:
        logger.warning("account-value probe crashed for %s/%s: %s", provider_id, key_id, exc)
        await update_upstream_key(
            db_path,
            key_id,
            {
                "account_value_status": str(AccountValueStatus.UNAVAILABLE),
                "account_value_error": str(exc),
                "account_value_checked_at": _now_iso(),
            },
        )
        return None

    fields: dict[str, Any] = {
        "account_value": json.dumps(value.to_dict(), separators=(",", ":")),
        "account_value_status": str(AccountValueStatus.OK),
        "account_value_error": None,
        "account_value_fetched_at": value.fetched_at,
        "account_value_checked_at": _now_iso(),
    }
    if value.credits is not None and value.credits.remaining is not None:
        fields["credits_remaining"] = value.credits.remaining
        fields["credits_total"] = value.credits.total
        fields["credits_used"] = value.credits.used
    await update_upstream_key(db_path, key_id, fields)
    return value
