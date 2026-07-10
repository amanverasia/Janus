"""Unit tests for the fusion combo strategy (panel fan-out + judge synthesis)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi import HTTPException

from janus.canonical.models import (
    CanonicalRequest,
    Message,
    Role,
    SystemBlock,
    TextPart,
    Tool,
    ToolFunction,
    ToolResult,
    ToolUse,
    Usage,
)
from janus.routing import fusion
from janus.routing.fusion import (
    PanelAnswer,
    build_judge_prompt,
    build_judge_request,
    build_panel_request,
    collect_panel,
    flatten_tool_history,
    run_fusion,
)
from janus.storage.settings import (
    SERVER_SETTING_DEFAULTS,
    resolve_combo_fusion_hard_timeout_s,
    resolve_combo_fusion_judge,
    resolve_combo_fusion_min_panel,
    resolve_combo_fusion_straggler_grace_s,
    resolve_combo_strategy,
)


def _req(**overrides: Any) -> CanonicalRequest:
    base: dict[str, Any] = {
        "model": "combo-x",
        "messages": [Message(role=Role.USER, content="hi")],
    }
    base.update(overrides)
    return CanonicalRequest(**base)


# ---------------------------------------------------------------------------
# Settings resolvers
# ---------------------------------------------------------------------------


def test_fusion_setting_defaults_registered():
    assert SERVER_SETTING_DEFAULTS["combo_fusion_min_panel"] == "2"
    assert SERVER_SETTING_DEFAULTS["combo_fusion_straggler_grace_s"] == "8"
    assert SERVER_SETTING_DEFAULTS["combo_fusion_hard_timeout_s"] == "90"
    assert SERVER_SETTING_DEFAULTS["combo_fusion_judge"] == ""


def test_fusion_resolvers_defaults_and_guards():
    assert resolve_combo_fusion_min_panel({}) == 2
    assert resolve_combo_fusion_min_panel({"combo_fusion_min_panel": "3"}) == 3
    assert resolve_combo_fusion_min_panel({"combo_fusion_min_panel": "junk"}) == 2
    assert resolve_combo_fusion_straggler_grace_s({}) == 8.0
    assert resolve_combo_fusion_straggler_grace_s({"combo_fusion_straggler_grace_s": "2.5"}) == 2.5
    assert resolve_combo_fusion_straggler_grace_s({"combo_fusion_straggler_grace_s": "nah"}) == 8.0
    assert resolve_combo_fusion_hard_timeout_s({}) == 90.0
    assert resolve_combo_fusion_hard_timeout_s({"combo_fusion_hard_timeout_s": "30"}) == 30.0
    assert resolve_combo_fusion_hard_timeout_s({"combo_fusion_hard_timeout_s": "x"}) == 90.0
    assert resolve_combo_fusion_judge({}) == ""
    assert resolve_combo_fusion_judge({"combo_fusion_judge": " a/m "}) == "a/m"


def test_resolve_combo_strategy_whitelist():
    assert resolve_combo_strategy({}) == "fallback"
    assert resolve_combo_strategy({"combo_strategy": "fallback"}) == "fallback"
    assert resolve_combo_strategy({"combo_strategy": "round_robin"}) == "round_robin"
    assert resolve_combo_strategy({"combo_strategy": "fusion"}) == "fusion"
    assert resolve_combo_strategy({"combo_strategy": "garbage"}) == "fallback"


# ---------------------------------------------------------------------------
# flatten_tool_history / build_panel_request
# ---------------------------------------------------------------------------


def test_flatten_tool_role_message_becomes_assistant_text():
    req = _req(
        messages=[
            Message(role=Role.USER, content="do it"),
            Message(
                role=Role.ASSISTANT,
                content=[ToolUse(id="t1", name="get_weather", input={})],
            ),
            Message(
                role=Role.TOOL,
                content=[ToolResult(tool_use_id="t1", content="sunny, 21C")],
            ),
        ]
    )
    flat = flatten_tool_history(req)
    assert flat.messages[0].content == "do it"
    assert flat.messages[1].role == Role.ASSISTANT
    assert flat.messages[1].content == "[Called tools: get_weather]"
    assert flat.messages[2].role == Role.ASSISTANT
    assert flat.messages[2].content == "[Tool result: sunny, 21C]"


def test_flatten_assistant_text_plus_tool_use():
    req = _req(
        messages=[
            Message(
                role=Role.ASSISTANT,
                content=[
                    TextPart(text="checking now"),
                    ToolUse(id="t1", name="search", input={}),
                    ToolUse(id="t2", name="fetch", input={}),
                ],
            ),
        ]
    )
    flat = flatten_tool_history(req)
    assert flat.messages[0].content == "checking now\n[Called tools: search, fetch]"


def test_flatten_truncates_tool_result_to_500_chars():
    long_text = "x" * 1200
    req = _req(
        messages=[
            Message(
                role=Role.TOOL,
                content=[ToolResult(tool_use_id="t1", content=long_text)],
            ),
        ]
    )
    flat = flatten_tool_history(req)
    content = flat.messages[0].content
    assert isinstance(content, str)
    assert content == f"[Tool result: {'x' * 500}]"


def test_flatten_leaves_plain_messages_untouched():
    req = _req(
        messages=[
            Message(role=Role.USER, content="hello"),
            Message(role=Role.ASSISTANT, content=[TextPart(text="hi there")]),
        ]
    )
    flat = flatten_tool_history(req)
    assert flat.messages[0].content == "hello"
    assert flat.messages[1].content == [TextPart(text="hi there")]


def test_build_panel_request_strips_tools_and_stream():
    tool = Tool(function=ToolFunction(name="f", parameters={}))
    req = _req(stream=True, tools=[tool], tool_choice={"type": "auto"})
    panel_req = build_panel_request(req)
    assert panel_req.tools == []
    assert panel_req.tool_choice is None
    assert panel_req.stream is False
    # Original untouched
    assert req.stream is True
    assert req.tools == [tool]


# ---------------------------------------------------------------------------
# Judge prompt / judge request
# ---------------------------------------------------------------------------


def _answer(model: str, text: str) -> PanelAnswer:
    return PanelAnswer(model=model, text=text, usage=Usage())


def test_judge_prompt_has_anonymous_sources_not_model_names():
    prompt = build_judge_prompt(
        [_answer("openai/gpt-x", "Answer A"), _answer("anthropic/claude-y", "Answer B")]
    )
    assert "[Source 1]" in prompt
    assert "[Source 2]" in prompt
    assert "Answer A" in prompt
    assert "Answer B" in prompt
    assert "gpt-x" not in prompt
    assert "claude-y" not in prompt
    assert "openai" not in prompt
    assert "anthropic" not in prompt


def test_build_judge_request_appends_user_turn_keeps_stream_and_tools():
    tool = Tool(function=ToolFunction(name="f", parameters={}))
    req = _req(
        stream=True,
        tools=[tool],
        system=[SystemBlock(text="be nice")],
        messages=[Message(role=Role.USER, content="original question")],
    )
    judge_req = build_judge_request(req, [_answer("a/m1", "one"), _answer("b/m2", "two")])
    assert judge_req.stream is True
    assert judge_req.tools == [tool]
    assert judge_req.system == req.system
    assert len(judge_req.messages) == 2
    assert judge_req.messages[0].content == "original question"
    last = judge_req.messages[-1]
    assert last.role == Role.USER
    assert isinstance(last.content, str)
    assert "[Source 1]" in last.content
    assert "[Source 2]" in last.content


# ---------------------------------------------------------------------------
# Quorum-grace collection
# ---------------------------------------------------------------------------


async def _later(delay: float, value: PanelAnswer | None) -> PanelAnswer | None:
    await asyncio.sleep(delay)
    return value


async def test_collect_panel_quorum_then_grace_cancels_straggler():
    fast1 = asyncio.create_task(_later(0.0, _answer("a/m1", "one")))
    fast2 = asyncio.create_task(_later(0.01, _answer("b/m2", "two")))
    slow = asyncio.create_task(_later(30.0, _answer("c/m3", "three")))
    start = time.monotonic()
    answers = await collect_panel(
        [fast1, fast2, slow], min_panel=2, straggler_grace_s=0.05, hard_timeout_s=10.0
    )
    elapsed = time.monotonic() - start
    assert [a.model for a in answers] == ["a/m1", "b/m2"]
    assert elapsed < 2.0
    assert slow.cancelled()


async def test_collect_panel_all_results_when_everyone_fast():
    tasks = [
        asyncio.create_task(_later(0.0, _answer("a/m1", "one"))),
        asyncio.create_task(_later(0.0, _answer("b/m2", "two"))),
        asyncio.create_task(_later(0.01, _answer("c/m3", "three"))),
    ]
    answers = await collect_panel(tasks, min_panel=2, straggler_grace_s=5.0, hard_timeout_s=10.0)
    assert len(answers) == 3


async def test_collect_panel_failures_do_not_count_toward_quorum():
    async def boom() -> PanelAnswer | None:
        raise RuntimeError("nope")

    tasks = [
        asyncio.create_task(boom()),
        asyncio.create_task(_later(0.0, None)),
        asyncio.create_task(_later(0.02, _answer("c/m3", "three"))),
    ]
    answers = await collect_panel(tasks, min_panel=2, straggler_grace_s=0.05, hard_timeout_s=5.0)
    assert [a.model for a in answers] == ["c/m3"]


async def test_collect_panel_hard_timeout_cuts_everything():
    tasks = [
        asyncio.create_task(_later(30.0, _answer("a/m1", "one"))),
        asyncio.create_task(_later(30.0, _answer("b/m2", "two"))),
    ]
    start = time.monotonic()
    answers = await collect_panel(tasks, min_panel=2, straggler_grace_s=1.0, hard_timeout_s=0.05)
    assert answers == []
    assert time.monotonic() - start < 2.0


# ---------------------------------------------------------------------------
# run_fusion decision logic (panel calls faked via monkeypatch)
# ---------------------------------------------------------------------------


def _fake_panel(results: dict[str, PanelAnswer | None]):
    async def fake(model: str, panel_req: CanonicalRequest, deps: Any) -> PanelAnswer | None:
        return results.get(model)

    return fake


def _deps() -> fusion.FusionDeps:
    return fusion.FusionDeps(
        handler=None,  # type: ignore[arg-type]
        providers={},
        resolve_format=lambda name: None,  # type: ignore[arg-type, return-value]
        db_path=":memory:",
        pricing_registry=None,  # type: ignore[arg-type]
        client_key_id=None,
        client_key_label=None,
    )


async def test_run_fusion_two_answers_returns_judge_request(monkeypatch):
    results = {
        "a/m1": _answer("a/m1", "alpha answer"),
        "b/m2": _answer("b/m2", "beta answer"),
    }
    monkeypatch.setattr(fusion, "_call_panel_model", _fake_panel(results))
    req = _req(stream=True)
    out = await run_fusion(
        req,
        ["a/m1", "b/m2"],
        judge_model="a/m1",
        deps=_deps(),
        min_panel=2,
        straggler_grace_s=0.1,
        hard_timeout_s=5.0,
    )
    assert out.model == "a/m1"
    assert out.stream is True
    last = out.messages[-1]
    assert isinstance(last.content, str)
    assert "[Source 1]" in last.content
    assert "alpha answer" in last.content
    assert "beta answer" in last.content


async def test_run_fusion_all_fail_raises_503(monkeypatch):
    monkeypatch.setattr(fusion, "_call_panel_model", _fake_panel({}))
    req = _req()
    with pytest.raises(HTTPException) as exc:
        await run_fusion(
            req,
            ["a/m1", "b/m2", "c/m3"],
            judge_model="a/m1",
            deps=_deps(),
            min_panel=2,
            straggler_grace_s=0.05,
            hard_timeout_s=5.0,
        )
    assert exc.value.status_code == 503


async def test_run_fusion_single_answer_skips_judge(monkeypatch):
    results = {"b/m2": _answer("b/m2", "only me")}
    monkeypatch.setattr(fusion, "_call_panel_model", _fake_panel(results))
    req = _req(stream=True)
    out = await run_fusion(
        req,
        ["a/m1", "b/m2", "c/m3"],
        judge_model="a/m1",
        deps=_deps(),
        min_panel=2,
        straggler_grace_s=0.05,
        hard_timeout_s=5.0,
    )
    # Original request pinned to the sole answering model — no judge turn appended.
    assert out.model == "b/m2"
    assert len(out.messages) == len(req.messages)
    assert out.stream is True
