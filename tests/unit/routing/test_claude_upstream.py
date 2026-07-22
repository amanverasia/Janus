from janus.routing.claude_beta import merge_anthropic_beta_values
from janus.routing.claude_normalize import (
    ClaudeUpstreamPrep,
    disable_thinking_if_tool_choice_forced,
    ensure_claude_thinking_display,
    extract_and_remove_betas,
    prepare_claude_upstream_body,
    strip_claude_billing_system_header,
)
from janus.routing.claude_oauth_tools import remap_oauth_tool_names, restore_oauth_tool_names
from janus.routing.claude_signing import sign_anthropic_messages_body


def test_merge_anthropic_beta_values_deduplicates() -> None:
    merged = merge_anthropic_beta_values(
        "oauth-2025-04-20,interleaved-thinking-2025-05-14",
        "oauth-2025-04-20,fast-mode-2026-02-01",
    )
    assert merged.count("oauth-2025-04-20") == 1
    assert "fast-mode-2026-02-01" in merged


def test_extract_and_remove_betas() -> None:
    body = {"betas": ["effort-2025-11-24"], "model": "claude-sonnet-4"}
    extra, out = extract_and_remove_betas(body)
    assert extra == ["effort-2025-11-24"]
    assert "betas" not in out


def test_disable_thinking_if_tool_choice_forced() -> None:
    body = {
        "thinking": {"type": "enabled", "budget_tokens": 1000},
        "output_config": {"effort": "high"},
        "tool_choice": {"type": "any"},
    }
    out = disable_thinking_if_tool_choice_forced(body)
    assert "thinking" not in out
    assert "output_config" not in out


def test_ensure_claude_thinking_display_defaults_summarized() -> None:
    body = {"thinking": {"type": "enabled", "budget_tokens": 1000}}
    out = ensure_claude_thinking_display(body)
    assert out["thinking"]["display"] == "summarized"


def test_strip_claude_billing_system_header() -> None:
    body = {
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cch=00000;"},
            {"type": "text", "text": "real system"},
        ]
    }
    out = strip_claude_billing_system_header(body)
    assert len(out["system"]) == 1
    assert out["system"][0]["text"] == "real system"


def test_oauth_tool_rename_and_restore() -> None:
    body = {
        "tools": [{"name": "bash", "description": "run", "input_schema": {}}],
        "messages": [],
    }
    renamed, reverse = remap_oauth_tool_names(body)
    assert renamed["tools"][0]["name"] == "Bash"
    assert reverse["Bash"] == "bash"
    response = {"content": [{"type": "tool_use", "name": "Bash", "id": "1", "input": {}}]}
    restored = restore_oauth_tool_names(response, reverse)
    assert restored["content"][0]["name"] == "bash"


def test_sign_anthropic_messages_body_recomputes_cch() -> None:
    body = {
        "model": "claude-sonnet-4",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "system": [
            {
                "type": "text",
                "text": "x-anthropic-billing-header: cch=00000; other=1;",
            }
        ],
    }
    signed = sign_anthropic_messages_body(body)
    text = signed["system"][0]["text"]
    assert "cch=00000" not in text
    assert "cch=" in text


def test_prepare_claude_upstream_body_oauth_pipeline() -> None:
    body = {
        "betas": ["effort-2025-11-24"],
        "thinking": {"type": "enabled", "budget_tokens": 1000},
        "tool_choice": {"type": "auto"},
        "tools": [{"name": "glob", "description": "find", "input_schema": {}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    out, prep = prepare_claude_upstream_body(
        body,
        "claude-sonnet-4",
        provider_prefix="claude",
        oauth_upstream=True,
    )
    assert isinstance(prep, ClaudeUpstreamPrep)
    assert prep.extra_betas == ["effort-2025-11-24"]
    assert out["tools"][0]["name"] == "Glob"
    assert out["thinking"]["display"] == "summarized"
