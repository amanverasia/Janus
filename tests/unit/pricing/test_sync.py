import httpx
import pytest
import respx

from janus.pricing.models import ModelPricing
from janus.pricing.sync import (
    LITELLM_URL,
    OPENROUTER_URL,
    PricingSyncError,
    fetch_and_sync,
    merge_sources,
    parse_litellm,
    parse_openrouter,
)
from janus.storage.database import init_db
from janus.storage.pricing_catalog import get_catalog, replace_catalog
from janus.storage.settings import get_setting


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


# --- parse_litellm ---------------------------------------------------------


def test_parse_litellm_basic_chat_model():
    data = {
        "gpt-5": {
            "input_cost_per_token": 2.8e-07,
            "output_cost_per_token": 1.1e-06,
            "mode": "chat",
        }
    }
    result = parse_litellm(data)
    assert "gpt-5" in result
    pricing = result["gpt-5"]
    assert pricing.input_per_mtok == pytest.approx(0.28)
    assert pricing.output_per_mtok == pytest.approx(1.1)
    assert pricing.cache_creation_per_mtok == 0.0
    assert pricing.cache_read_per_mtok == 0.0


def test_parse_litellm_skips_sample_spec():
    data = {
        "sample_spec": {
            "input_cost_per_token": 1.0,
            "output_cost_per_token": 1.0,
            "mode": "chat",
        },
        "real-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }
    result = parse_litellm(data)
    assert "sample_spec" not in result
    assert "real-model" in result


def test_parse_litellm_skips_non_chat_modes():
    data = {
        "embed-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 0,
            "mode": "embedding",
        },
        "image-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 0,
            "mode": "image_generation",
        },
        "audio-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 0,
            "mode": "audio_transcription",
        },
        "rerank-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 0,
            "mode": "rerank",
        },
        "chat-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
        "responses-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "responses",
        },
        "completion-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "completion",
        },
        "no-mode-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
        },
    }
    result = parse_litellm(data)
    assert "embed-model" not in result
    assert "image-model" not in result
    assert "audio-model" not in result
    assert "rerank-model" not in result
    assert "chat-model" in result
    assert "responses-model" in result
    assert "completion-model" in result
    assert "no-mode-model" in result


def test_parse_litellm_cache_fields_and_default():
    data = {
        "cached-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "cache_creation_input_token_cost": 1.25e-06,
            "cache_read_input_token_cost": 1e-07,
            "mode": "chat",
        },
        "no-cache-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }
    result = parse_litellm(data)
    assert result["cached-model"].cache_creation_per_mtok == pytest.approx(1.25)
    assert result["cached-model"].cache_read_per_mtok == pytest.approx(0.1)
    assert result["no-cache-model"].cache_creation_per_mtok == 0.0
    assert result["no-cache-model"].cache_read_per_mtok == 0.0


def test_parse_litellm_dual_key_full_and_bare():
    data = {
        "anthropic/claude-9-opus": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        }
    }
    result = parse_litellm(data)
    assert "anthropic/claude-9-opus" in result
    assert "claude-9-opus" in result
    assert result["claude-9-opus"] == result["anthropic/claude-9-opus"]


def test_parse_litellm_bare_key_first_writer_wins_in_sorted_order():
    # sorted order: "a/model-x" before "b/model-x" -- "a/..." should win the
    # bare "model-x" slot since it's iterated first.
    data = {
        "b-provider/model-x": {
            "input_cost_per_token": 9e-06,
            "output_cost_per_token": 9e-06,
            "mode": "chat",
        },
        "a-provider/model-x": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }
    result = parse_litellm(data)
    assert result["model-x"].input_per_mtok == pytest.approx(1.0)


def test_parse_litellm_skips_non_numeric_cost_entries():
    data = {
        "broken-model": {
            "input_cost_per_token": "not-a-number",
            "output_cost_per_token": None,
            "mode": "chat",
        }
    }
    result = parse_litellm(data)
    assert "broken-model" not in result


def test_parse_litellm_skips_negative_cost_entries():
    data = {
        "negative-input": {
            "input_cost_per_token": -1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
        "negative-output": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": -2e-06,
            "mode": "chat",
        },
        "fine-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }
    result = parse_litellm(data)
    assert "negative-input" not in result
    assert "negative-output" not in result
    assert "fine-model" in result


def test_parse_litellm_skips_unhashable_mode():
    data = {
        "weird-mode-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": ["chat"],
        },
        "fine-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }
    result = parse_litellm(data)
    assert "weird-mode-model" not in result
    assert "fine-model" in result


# --- parse_openrouter -------------------------------------------------------


def test_parse_openrouter_basic():
    data = {
        "data": [
            {
                "id": "deepseek/deepseek-v4-pro",
                "pricing": {
                    "prompt": "0.000000435",
                    "completion": "0.00000087",
                    "input_cache_read": "0.0000001",
                    "input_cache_write": "0.0000002",
                },
            }
        ]
    }
    result = parse_openrouter(data)
    assert "deepseek/deepseek-v4-pro" in result
    assert "deepseek-v4-pro" in result
    pricing = result["deepseek/deepseek-v4-pro"]
    assert pricing.input_per_mtok == pytest.approx(0.435)
    assert pricing.output_per_mtok == pytest.approx(0.87)
    assert pricing.cache_read_per_mtok == pytest.approx(0.1)
    assert pricing.cache_creation_per_mtok == pytest.approx(0.2)


def test_parse_openrouter_skips_free_models():
    data = {
        "data": [
            {
                "id": "free/model",
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {
                "id": "paid/model",
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            },
        ]
    }
    result = parse_openrouter(data)
    assert "free/model" not in result
    assert "paid/model" in result
    # bare suffix "model" is claimed by paid/model, not the skipped free one
    assert result["model"].input_per_mtok == pytest.approx(1.0)


def test_parse_openrouter_skips_unparseable_pricing():
    data = {
        "data": [
            {
                "id": "weird/model",
                "pricing": {"prompt": "not-a-number", "completion": "0.000002"},
            }
        ]
    }
    result = parse_openrouter(data)
    assert "weird/model" not in result


def test_parse_openrouter_skips_negative_cost_entries():
    data = {
        "data": [
            {
                "id": "negative/prompt",
                "pricing": {"prompt": "-0.000001", "completion": "0.000002"},
            },
            {
                "id": "negative/completion",
                "pricing": {"prompt": "0.000001", "completion": "-0.000002"},
            },
            {
                "id": "fine/model",
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            },
        ]
    }
    result = parse_openrouter(data)
    assert "negative/prompt" not in result
    assert "negative/completion" not in result
    assert "fine/model" in result


def test_parse_openrouter_missing_cache_fields_default_zero():
    data = {
        "data": [
            {
                "id": "simple/model",
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            }
        ]
    }
    result = parse_openrouter(data)
    pricing = result["simple/model"]
    assert pricing.cache_read_per_mtok == 0.0
    assert pricing.cache_creation_per_mtok == 0.0


# --- merge_sources -----------------------------------------------------------


def test_merge_sources_litellm_wins_on_collision():
    litellm = {"m": ModelPricing(1.0, 2.0, 0.0, 0.0)}
    openrouter = {
        "m": ModelPricing(9.0, 9.0, 0.0, 0.0),
        "only-or": ModelPricing(3.0, 4.0, 0.0, 0.0),
    }
    merged = merge_sources(litellm, openrouter)
    assert merged["m"].input_per_mtok == 1.0
    assert merged["only-or"].input_per_mtok == 3.0


def test_merge_sources_empty_inputs():
    assert merge_sources({}, {}) == {}


# --- fetch_and_sync ------------------------------------------------------------


def _litellm_payload():
    return {
        "sample_spec": {"input_cost_per_token": 1, "output_cost_per_token": 1, "mode": "chat"},
        "litellm-model": {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        },
    }


def _openrouter_payload():
    return {
        "data": [
            {
                "id": "or-provider/or-model",
                "pricing": {"prompt": "0.000003", "completion": "0.000004"},
            }
        ]
    }


@respx.mock
async def test_fetch_and_sync_writes_rows_and_settings(db):
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(200, json=_litellm_payload()))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=_openrouter_payload()))

    count = await fetch_and_sync(db)

    assert count == 3  # litellm-model (no slash, no bare) + or-model full+bare
    catalog = await get_catalog(db)
    assert "litellm-model" in catalog
    assert "or-provider/or-model" in catalog
    assert "or-model" in catalog
    assert (await get_setting(db, "pricing_catalog_count")) == str(count)
    assert await get_setting(db, "pricing_last_sync_at") is not None


@respx.mock
async def test_fetch_and_sync_one_source_down_still_syncs(db):
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(200, json=_litellm_payload()))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))

    count = await fetch_and_sync(db)

    catalog = await get_catalog(db)
    assert "litellm-model" in catalog
    assert count == len(catalog)


@respx.mock
async def test_fetch_and_sync_both_down_raises_and_leaves_catalog_untouched(db):
    await replace_catalog(
        db,
        [
            {
                "model": "existing-model",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )

    respx.get(LITELLM_URL).mock(return_value=httpx.Response(500))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(PricingSyncError):
        await fetch_and_sync(db)

    catalog = await get_catalog(db)
    assert "existing-model" in catalog
    assert len(catalog) == 1


@respx.mock
async def test_fetch_and_sync_both_sources_parse_empty_raises(db):
    await replace_catalog(
        db,
        [
            {
                "model": "existing-model",
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
                "source": "litellm",
            }
        ],
    )

    # Both sources return well-formed but empty/unusable payloads.
    respx.get(LITELLM_URL).mock(return_value=httpx.Response(200, json={"sample_spec": {}}))
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    with pytest.raises(PricingSyncError):
        await fetch_and_sync(db)

    catalog = await get_catalog(db)
    assert "existing-model" in catalog
    assert len(catalog) == 1
