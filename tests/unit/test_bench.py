from __future__ import annotations

import datetime
import json

import httpx
import pytest

from janus.bench import (
    BenchResult,
    BenchRun,
    build_markdown_report,
    list_bench_models,
    run_bench,
    select_models,
    stream_bench_request,
)


def _sse(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="http://bench.test")


def _stream_lines(tokens: int = 4) -> list[str]:
    lines = []
    for i in range(tokens):
        lines.append(
            "data: "
            + json.dumps(
                {
                    "id": "b1",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "x"}}],
                }
            )
        )
    lines.append(
        "data: "
        + json.dumps(
            {
                "id": "b1",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": tokens,
                    "total_tokens": 10 + tokens,
                },
            }
        )
    )
    lines.append("data: [DONE]")
    return lines


async def test_list_bench_models_parses_catalog():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200, json={"data": [{"id": "b/second"}, {"id": "a/first"}, {"id": None}]}
        )

    async with _client(handler) as client:
        models = await list_bench_models(client)
    assert models == ["a/first", "b/second"]


def test_select_models_filters_and_validates():
    available = ["a/one", "a/two", "b/three"]
    assert select_models(available, prefix="a") == ["a/one", "a/two"]
    assert select_models(available, requested="b/three, a/one") == ["a/one", "b/three"]
    assert select_models(available, limit=2) == ["a/one", "a/two"]
    with pytest.raises(ValueError, match="not routable"):
        select_models(available, requested="missing/model")


@pytest.mark.asyncio
async def test_stream_bench_request_measures_ttft_tokens_and_tps():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["temperature"] == 0
        return httpx.Response(200, content=_sse(_stream_lines(tokens=4)))

    async with _client(handler) as client:
        result = await stream_bench_request(
            client, model="a/one", prompt="hi", prompt_index=0, max_tokens=16, timeout=10.0
        )
    assert result.status == 200
    assert result.error is None
    assert result.ttft_ms is not None and result.ttft_ms >= 0
    assert result.output_tokens == 4
    assert result.tps is not None and result.tps > 0


@pytest.mark.asyncio
async def test_stream_bench_request_records_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async with _client(handler) as client:
        result = await stream_bench_request(
            client, model="a/one", prompt="hi", prompt_index=0, max_tokens=16, timeout=10.0
        )
    assert result.status == 500
    assert "boom" in (result.error or "")
    assert result.output_tokens is None


@pytest.mark.asyncio
async def test_run_bench_and_report():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(_stream_lines(tokens=3)))

    async with _client(handler) as client:
        run = await run_bench(
            client,
            base_url="http://bench.test",
            prompts=2,
            max_tokens=16,
            timeout=10.0,
            selected_models=["a/one", "b/two"],
        )
    assert len(run.results) == 4
    report = build_markdown_report(run)
    assert "# janus bench report" in report
    assert "`a/one` | 2/2" in report
    assert "`b/two` | 2/2" in report
    assert "no local pricing registry" in report.lower()
    assert report.count("| 3 |") >= 2


def test_report_marks_failures_and_missing_costs():
    run = BenchRun(
        base_url="http://bench.test",
        started_at=datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.UTC),
        prompts=1,
        max_tokens=16,
        results=[
            BenchResult(
                model="a/one",
                prompt_index=0,
                status=200,
                total_ms=100,
                ttft_ms=20,
                output_tokens=5,
                tps=60.0,
            ),
            BenchResult(model="a/two", prompt_index=0, status=429, error="rate limited | retry"),
        ],
    )
    report = build_markdown_report(run)
    assert "`a/one` | 1/1" in report
    assert "`a/two` | 0/1" in report
    assert "rate limited \\| retry" in report
    assert report.rstrip().endswith("omitted.")
