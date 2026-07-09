from janus.catalog import PROVIDERS, gateway_entries, inventory_to_gateway_map
from janus.routing.model_caps import get_model_capabilities


def test_minimax_moonshot_zhipu_gateways_exist():
    gws = gateway_entries()
    assert "minimax" in gws
    assert "moonshot" in gws
    assert "zhipu" in gws
    assert gws["minimax"]["prefix"] == "minimax"
    assert gws["moonshot"]["prefix"] == "moonshot"
    assert gws["zhipu"]["prefix"] == "zhipu"
    assert gws["minimax"]["api_type"] == "openai_compat"
    assert any(t.get("format") == "anthropic" for t in gws["minimax"].get("transports", []))


def test_inventory_ids_map_to_themselves_when_same():
    # moonshot/minimax/zhipu share inventory id == gateway id
    m = inventory_to_gateway_map()
    assert "minimax" not in m  # same id, no bridge needed
    assert "moonshot" not in m
    assert "zhipu" not in m
    assert m.get("google") == "gemini"
    assert m.get("dashscope") == "qwen"


def test_anthropic_reasoning_caps():
    caps = get_model_capabilities("anthropic", "claude-sonnet-4-20250514")
    assert caps["reasoning"] is True
    assert caps["thinking_format"] in {"claude-budget", "claude-adaptive"}


def test_catalog_reasoning_flags():
    assert PROVIDERS["anthropic"]["capabilities"].get("reasoning") is True
    assert PROVIDERS["xai"]["capabilities"].get("reasoning") is True
