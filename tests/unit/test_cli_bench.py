from __future__ import annotations

import datetime
import re

from typer.testing import CliRunner

import janus.bench as bench_module
from janus.bench import BenchResult, BenchRun
from janus.cli import app

runner = CliRunner()


def _run(monkeypatch, models: list[str]) -> None:
    async def fake_list(client):  # type: ignore[no-untyped-def]
        return models

    monkeypatch.setattr(bench_module, "list_bench_models", fake_list)


def test_bench_help_lists_options():
    result = runner.invoke(app, ["bench", "run", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    for flag in ("--base-url", "--api-key", "--models", "--prefix", "--limit", "--output", "--yes"):
        assert flag in plain


def test_bench_exits_when_no_models(monkeypatch):
    _run(monkeypatch, [])
    result = runner.invoke(app, ["bench", "run", "--api-key", "sk-test"])
    assert result.exit_code == 2
    assert "No models matched" in result.output


def test_bench_confirm_declined_sends_nothing(monkeypatch):
    _run(monkeypatch, ["a/one"])

    async def fail_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("run_bench must not be called after declining")

    monkeypatch.setattr(bench_module, "run_bench", fail_run)
    result = runner.invoke(app, ["bench", "run", "--api-key", "sk-test"], input="n\n")
    assert result.exit_code == 1


def test_bench_yes_writes_report(monkeypatch, tmp_path):
    _run(monkeypatch, ["a/one"])
    fake_run = BenchRun(
        base_url="http://127.0.0.1:20128",
        started_at=datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.UTC),
        prompts=1,
        max_tokens=64,
        results=[
            BenchResult(
                model="a/one",
                prompt_index=0,
                status=200,
                total_ms=250,
                ttft_ms=40,
                output_tokens=9,
                tps=42.0,
            )
        ],
    )

    async def fake_run_bench(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_run

    monkeypatch.setattr(bench_module, "run_bench", fake_run_bench)
    out = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "bench",
            "run",
            "--api-key",
            "sk-test",
            "--yes",
            "--config",
            "/nonexistent/config.yaml",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    report = out.read_text()
    assert "# janus bench report" in report
    assert "`a/one` | 1/1" in report
    assert "omitted." in report
