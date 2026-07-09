from janus.catalog import gateway_entries
from janus.config.schema import ProviderConfig
from janus.providers.anthropic import ANTHROPIC_CLI_BETA_HEADERS
from janus.providers.registry import PREFIX_ALIASES, ProviderRegistry
from janus.routing.model_aliases import resolve_model_alias
from janus.routing.model_caps import get_model_capabilities
from janus.routing.thinking import apply_thinking_to_payload


def test_dual_base_gateways_exist() -> None:
    gws = gateway_entries()
    assert gws["minimax"]["base_url"].startswith("https://api.minimaxi.com")
    assert gws["minimax_io"]["base_url"].startswith("https://api.minimax.io")
    assert gws["minimax_io"]["prefix"] == "minimax-io"
    assert gws["kimi_coding"]["prefix"] == "kimi"
    assert gws["kimi_coding"]["base_url"].startswith("https://api.kimi.com/coding")
    assert gws["glm_coding"]["prefix"] == "glm"
    assert "api.z.ai" in gws["glm_coding"]["base_url"]
    # Coding gateways must not share moonshot's openai transport override
    assert "transports" not in gws["moonshot"] or "openai" not in (
        gws["moonshot"].get("transports") or {}
    )


def test_mimo_prefix_alias_routes_to_xiaomi() -> None:
    assert PREFIX_ALIASES["mimo"] == "xiaomi"
    reg = ProviderRegistry()
    reg.register(
        ProviderConfig(
            id="xiaomi",
            prefix="xiaomi",
            api_type="openai_compat",
            base_url="https://api.xiaomimimo.com/v1",
            api_key="k",
            models=["mimo-v2.5"],
        )
    )
    targets = reg.lookup("mimo/mimo-v2.5")
    assert targets is not None
    assert targets[0].prefix == "xiaomi"
    assert targets[0].model == "mimo-v2.5"


def test_mimo_claude_alias() -> None:
    up, intent = resolve_model_alias("xiaomi", "mimo-v2.5-pro-claude")
    assert up == "mimo-v2.5-pro"
    assert intent is None


def test_claude_adaptive_sets_thinking_and_effort() -> None:
    payload: dict = {"model": "claude-sonnet-4-5", "messages": []}
    apply_thinking_to_payload(
        payload,
        target_format="anthropic",
        model="claude-sonnet-4-5",
        caps={"reasoning": True, "thinking_format": "claude-adaptive"},
        intent={"mode": "level", "level": "high"},
    )
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "high"}


def test_gemini3_level_none_clamps_to_minimal() -> None:
    payload: dict = {"model": "gemini-3-flash-preview", "contents": []}
    apply_thinking_to_payload(
        payload,
        target_format="gemini",
        model="gemini-3-flash-preview",
        caps={
            "reasoning": True,
            "thinking_format": "gemini-level",
            "thinking_can_disable": False,
        },
        intent={"mode": "none"},
    )
    tc = payload["generationConfig"]["thinkingConfig"]
    assert tc["thinkingLevel"] == "minimal"
    assert tc["includeThoughts"] is False


def test_gemini2_budget_auto_is_dynamic() -> None:
    payload: dict = {"model": "gemini-2.5-flash", "contents": []}
    apply_thinking_to_payload(
        payload,
        target_format="gemini",
        model="gemini-2.5-flash",
        caps={"reasoning": True, "thinking_format": "gemini-budget"},
        intent={"mode": "auto"},
    )
    tc = payload["generationConfig"]["thinkingConfig"]
    assert tc["thinkingBudget"] == -1
    assert tc["includeThoughts"] is True


def test_minimax_m2_cannot_disable() -> None:
    payload: dict = {"model": "MiniMax-M2.5", "messages": []}
    apply_thinking_to_payload(
        payload,
        target_format="openai",
        model="MiniMax-M2.5",
        caps={
            "reasoning": True,
            "thinking_format": "minimax",
            "thinking_can_disable": False,
        },
        intent={"mode": "none"},
    )
    assert payload["thinking"]["type"] == "adaptive"


def test_mimo_omni_and_tts_caps() -> None:
    omni = get_model_capabilities("xiaomi", "mimo-v2-omni")
    assert omni.get("audio_input") is True
    assert omni.get("audio_output") is True
    tts = get_model_capabilities("xmtp", "mimo-v2.5-tts")
    assert tts.get("audio_output") is True
    pro = get_model_capabilities("xiaomi", "mimo-v2.5-pro")
    assert pro.get("context_window") == 1_000_000


def test_anthropic_cli_beta_headers_include_effort() -> None:
    assert "effort-2025-11-24" in ANTHROPIC_CLI_BETA_HEADERS
    assert "interleaved-thinking-2025-05-14" in ANTHROPIC_CLI_BETA_HEADERS
