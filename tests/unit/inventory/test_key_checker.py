import pytest
import respx
from httpx import Response

from janus.inventory.key_checker import (
    check_upstream_key,
    compute_health_status,
    validate_key,
)
from janus.storage.database import init_db
from janus.storage.upstream_keys import create_upstream_key, get_upstream_key
from janus.storage.upstream_models import list_models_for_key


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_openai_success():
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
            headers={"x-ratelimit-limit-requests": "500"},
        )
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )

    result = await validate_key("sk-proj-test", "openai")
    assert result["is_valid"] is True
    assert result["rate_limit_rpm"] == 500
    assert len(result["models"]) == 2
    assert result["is_usable"] is True


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_auth_failure():
    respx.get("https://api.openai.com/v1/models").mock(return_value=Response(401, json={}))

    result = await validate_key("sk-proj-bad", "openai", skip_probe=True)
    assert result["is_valid"] is False
    assert "Auth failed" in result["error"]


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_openrouter_credit_check():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "openai/gpt-4o"}]},
            headers={
                "x-credits-remaining": "12.50",
                "x-credits-used": "7.50",
                "x-credits-limit": "20.00",
            },
        )
    )
    respx.get("https://openrouter.ai/api/v1/key").mock(
        return_value=Response(
            200,
            json={"data": {"limit": 20, "limit_remaining": 12.5, "usage": 7.5}},
        )
    )

    result = await validate_key("sk-or-v1-test", "openrouter", skip_probe=True)
    assert result["is_valid"] is True
    assert result["credits_remaining"] == 12.5
    assert result["credits_total"] == 20.0


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_deepseek_cny_converted_to_usd(monkeypatch):
    monkeypatch.setenv("INVENTORY_CNY_USD_RATE", "0.1")
    respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "deepseek-chat"}]})
    )
    respx.get("https://api.deepseek.com/user/balance").mock(
        return_value=Response(
            200,
            json={
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "9558.21",
                        "granted_balance": "10000.00",
                        "topped_up_balance": "0.00",
                    }
                ]
            },
        )
    )

    result = await validate_key("sk-" + "a" * 20, "deepseek", skip_probe=True)
    assert result["is_valid"] is True
    assert result["credits_remaining"] == pytest.approx(955.82)
    assert result["credits_total"] == pytest.approx(1000.0)
    assert result["metadata"]["credits_currency"] == "CNY"


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_rate_limited_partial_check():
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=Response(429, headers={"x-ratelimit-limit-requests": "3"})
    )

    result = await validate_key("sk-proj-test", "openai", skip_probe=True)
    assert result["is_valid"] is True
    assert result["partial_check"] is True
    assert result["rate_limit_rpm"] == 3


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_nvidia_via_chat():
    respx.get("https://integrate.api.nvidia.com/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "meta/llama-3.1-8b-instruct"}]})
    )
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )

    result = await validate_key("nvapi-test", "nvidia")
    assert result["is_valid"] is True
    assert result["is_usable"] is True


@pytest.mark.asyncio
async def test_validate_key_blocks_private_url():
    result = await validate_key(
        "sk-test",
        "custom",
        {"custom_base_url": "http://127.0.0.1/v1"},
        skip_probe=True,
    )
    assert result["is_valid"] is False
    assert "Blocked endpoint" in result["error"]


def test_compute_health_status_exhausted():
    result = {"is_valid": True, "credits_remaining": 0.0}
    compute_health_status(result)
    assert result["health_status"] == "exhausted"
    assert any("exhausted" in warning.lower() for warning in result["health_warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_check_upstream_key_updates_db_and_models(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-test",
    )

    respx.get("https://api.openai.com/v1/models").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        )
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-test"})
    )

    await check_upstream_key(db_path, record["id"])
    updated = await get_upstream_key(db_path, record["id"])
    assert updated is not None
    assert updated["status"] == "active"
    assert updated["is_valid"] == 1

    models = await list_models_for_key(db_path, record["id"])
    assert len(models) == 2
    assert models[0]["model_id"] in {"gpt-4o", "gpt-4o-mini"}


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_xai_quota_exhausted_not_auth_failure():
    body = {
        "code": "permission-denied",
        "error": (
            "Your team has either used all available credits or reached its monthly spending limit."
        ),
    }
    respx.get("https://api.x.ai/v1/models").mock(return_value=Response(403, json=body))

    result = await validate_key("xai-test-key", "xai", skip_probe=True)
    assert result["is_valid"] is True
    assert result["is_usable"] is False
    assert result["usability_status"] == "no_quota"
    assert result["credits_remaining"] == 0.0
    assert result["health_status"] == "exhausted"


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_xai_probe_skips_media_models_and_uses_chat():
    respx.get("https://api.x.ai/v1/models").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": "grok-imagine-image"},
                    {"id": "grok-4.20-0309-non-reasoning"},
                    {"id": "grok-4"},
                ]
            },
        )
    )
    route = respx.post("https://api.x.ai/v1/chat/completions").mock(
        return_value=Response(200, json={"id": "chatcmpl-xai"})
    )

    result = await validate_key("xai-test-key", "xai")
    assert result["is_valid"] is True
    assert result["is_usable"] is True
    assert route.called
    sent = route.calls.last.request
    import json as _json

    payload = _json.loads(sent.content.decode())
    assert payload["model"] == "grok-4"


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_xai_probe_marks_quota_on_inference_403():
    respx.get("https://api.x.ai/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "grok-4"}]})
    )
    respx.post("https://api.x.ai/v1/chat/completions").mock(
        return_value=Response(
            403,
            json={
                "code": "permission-denied",
                "error": "used all available credits or reached its monthly spending limit",
            },
        )
    )

    result = await validate_key("xai-test-key", "xai")
    assert result["is_valid"] is True
    assert result["is_usable"] is False
    assert result["usability_status"] == "no_quota"


@pytest.mark.asyncio
@respx.mock
async def test_check_upstream_key_marks_invalid(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(
        db_path,
        provider_id="groq",
        key_value="gsk_bad",
    )
    respx.get("https://api.groq.com/openai/v1/models").mock(return_value=Response(403, json={}))

    await check_upstream_key(db_path, record["id"])
    updated = await get_upstream_key(db_path, record["id"])
    assert updated is not None
    assert updated["status"] == "invalid"
    assert updated["is_valid"] == 0
    assert updated["last_error"] is not None


def test_multi_base_candidates_minimax_and_moonshot() -> None:
    from janus.inventory.key_checker import MULTI_BASE_CANDIDATES

    assert "minimax" in MULTI_BASE_CANDIDATES
    assert any("minimaxi.com" in b for b in MULTI_BASE_CANDIDATES["minimax"])
    assert any("minimax.io" in b for b in MULTI_BASE_CANDIDATES["minimax"])
    assert "moonshot" in MULTI_BASE_CANDIDATES
    # Coding host is a separate gateway — not an inventory multi-base candidate.
    assert not any("kimi.com" in b for b in MULTI_BASE_CANDIDATES["moonshot"])
