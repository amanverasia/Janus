from __future__ import annotations

import datetime
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PROMPTS: tuple[str, ...] = (
    "Reply with exactly: pong",
    "Summarize in one sentence: The Janus gateway routes OpenAI-compatible requests"
    " to many upstream providers and records cost, latency, and reliability signals"
    " for every attempt.",
    "Write a haiku about request routing.",
)
MAX_PROMPTS = len(DEFAULT_PROMPTS)


@dataclass
class BenchResult:
    model: str
    prompt_index: int
    status: int | None = None
    error: str | None = None
    ttft_ms: int | None = None
    total_ms: int = 0
    output_tokens: int | None = None
    tps: float | None = None


@dataclass
class BenchRun:
    base_url: str
    started_at: datetime.datetime
    prompts: int
    max_tokens: int
    results: list[BenchResult] = field(default_factory=list)
    estimated_costs: dict[str, float] = field(default_factory=dict)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


async def list_bench_models(client: httpx.AsyncClient) -> list[str]:
    response = await client.get("/v1/models")
    response.raise_for_status()
    payload = response.json()
    models = [str(entry.get("id")) for entry in payload.get("data", []) if entry.get("id")]
    return sorted(models)


def select_models(
    available: list[str],
    *,
    requested: str = "",
    prefix: str = "",
    limit: int = 0,
) -> list[str]:
    selected = available
    if requested.strip():
        wanted = {name.strip() for name in requested.split(",") if name.strip()}
        selected = [name for name in selected if name in wanted]
        missing = sorted(wanted - set(selected))
        if missing:
            raise ValueError(f"models not routable for this key: {', '.join(missing)}")
    if prefix.strip():
        needle = prefix.strip()
        selected = [name for name in selected if name.startswith(needle)]
    if limit > 0:
        selected = selected[:limit]
    return selected


async def stream_bench_request(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompt: str,
    prompt_index: int,
    max_tokens: int,
    timeout: float,
) -> BenchResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    started = time.perf_counter()
    ttft_ms: int | None = None
    output_tokens: int | None = None
    status: int | None = None
    error: str | None = None
    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json=body,
            timeout=timeout,
        ) as response:
            status = response.status_code
            if status != 200:
                await response.aread()
                error = response.text[:200]
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    if ttft_ms is None and chunk.get("choices"):
                        for choice in chunk["choices"]:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                ttft_ms = int((time.perf_counter() - started) * 1000)
                                break
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                        output_tokens = int(usage["completion_tokens"])
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    total_ms = int((time.perf_counter() - started) * 1000)
    tps: float | None = None
    if output_tokens and ttft_ms is not None:
        elapsed_ms = max(total_ms - ttft_ms, 1)
        tps = output_tokens / (elapsed_ms / 1000.0)
    return BenchResult(
        model=model,
        prompt_index=prompt_index,
        status=status,
        error=error,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        output_tokens=output_tokens,
        tps=tps,
    )


def _model_rows(run: BenchRun) -> list[dict[str, Any]]:
    grouped: dict[str, list[BenchResult]] = {}
    for result in run.results:
        grouped.setdefault(result.model, []).append(result)
    rows = []
    for model in sorted(grouped):
        results = grouped[model]
        ok = [r for r in results if r.status == 200 and not r.error]
        ttft = _median([float(r.ttft_ms) for r in ok if r.ttft_ms is not None])
        tps = _median([r.tps for r in ok if r.tps is not None])
        rows.append(
            {
                "model": model,
                "ok": len(ok),
                "total": len(results),
                "median_ttft_ms": ttft,
                "median_tps": tps,
                "output_tokens": sum(r.output_tokens or 0 for r in ok),
                "median_total_ms": _median([float(r.total_ms) for r in ok]),
                "estimated_cost": run.estimated_costs.get(model),
                "errors": [r.error for r in results if r.error],
            }
        )
    return rows


def _fmt_ms(value: float | None) -> str:
    return f"{int(value)} ms" if value is not None else "—"


def _fmt_tps(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def _fmt_cost(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "—"


def build_markdown_report(run: BenchRun) -> str:
    rows = _model_rows(run)
    lines = [
        "# janus bench report",
        "",
        f"- Gateway: `{run.base_url}`",
        f"- Started: {run.started_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"- Prompts per model: {run.prompts} (deterministic, temperature 0, "
        f"max {run.max_tokens} output tokens)",
        "",
        "## Per-model summary",
        "",
        "| Model | OK | Median TTFT | Median TPS | Median total | Tokens | Est. cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['model']}` | {row['ok']}/{row['total']} | {_fmt_ms(row['median_ttft_ms'])}"
            f" | {_fmt_tps(row['median_tps'])} | {_fmt_ms(row['median_total_ms'])}"
            f" | {row['output_tokens']} | {_fmt_cost(row['estimated_cost'])} |"
        )
    lines += [
        "",
        "## Per-request detail",
        "",
        "| Model | Prompt | Status | TTFT | Total | Tokens | TPS | Error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in run.results:
        error = (result.error or "").replace("|", "\\|")
        ttft = _fmt_ms(float(result.ttft_ms)) if result.ttft_ms is not None else "—"
        tokens = result.output_tokens if result.output_tokens is not None else "—"
        lines.append(
            f"| `{result.model}` | {result.prompt_index + 1} | {result.status or '—'}"
            f" | {ttft} | {_fmt_ms(float(result.total_ms))} | {tokens}"
            f" | {_fmt_tps(result.tps)} | {error} |"
        )
    lines.append("")
    notes = [
        "Costs are estimated from this machine's pricing registry (builtin + local DB"
        " overrides); TTFT is measured to the first streamed content chunk."
    ]
    if not run.estimated_costs:
        notes = ["No local pricing registry was available, so cost estimates are omitted."]
    lines.append(f"> {notes[0]}")
    return "\n".join(lines) + "\n"


async def estimate_costs_from_db(db_path: Path, results: list[BenchResult]) -> dict[str, float]:
    from janus.canonical.models import Usage
    from janus.pricing.calculator import compute_cost
    from janus.pricing.registry import PricingRegistry
    from janus.storage.pricing_catalog import get_catalog
    from janus.storage.pricing_db import get_pricing_overrides

    overrides = await get_pricing_overrides(db_path)
    catalog = await get_catalog(db_path)
    registry = PricingRegistry(overrides, catalog)
    tokens: dict[str, Usage] = {}
    for result in results:
        if result.status != 200 or not result.output_tokens:
            continue
        model = result.model.split("/", 1)[1] if "/" in result.model else result.model
        slot = tokens.setdefault(model, Usage())
        slot.output_tokens += result.output_tokens
    return {
        model: round(compute_cost(usage, model, registry), 6) for model, usage in tokens.items()
    }


async def run_bench(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    prompts: int,
    max_tokens: int,
    timeout: float,
    selected_models: list[str],
    cost_db_path: Path | None = None,
) -> BenchRun:
    run = BenchRun(
        base_url=base_url,
        started_at=datetime.datetime.now(datetime.UTC),
        prompts=prompts,
        max_tokens=max_tokens,
    )
    prompt_set = DEFAULT_PROMPTS[:prompts]
    for model in selected_models:
        for index, prompt in enumerate(prompt_set):
            run.results.append(
                await stream_bench_request(
                    client,
                    model=model,
                    prompt=prompt,
                    prompt_index=index,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            )
    if cost_db_path is not None and cost_db_path.is_file() and run.results:
        try:
            run.estimated_costs = await estimate_costs_from_db(cost_db_path, run.results)
        except Exception:
            logger.warning("Cost estimation failed", exc_info=True)
    return run
