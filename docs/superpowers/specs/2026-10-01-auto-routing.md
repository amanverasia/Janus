# model="auto" routing + quality overrides/preview (#183, #187 part 2)

Date: 2026-10-01. Status: implementing per the user-approved #239 cluster order. Issues: #183, #187 (part 2), tracker #239.

## Goal

`model="auto"` resolves per request to a ranked model chain under a configurable strategy
(`balanced` | `cheapest` | `fastest` | `quality`, default `balanced`); the top-N chain becomes
the fallback try-order through the existing `resolve_attempts` pipeline. Operator overrides
win over measured scores, and an auto-preview (same ranker, no DRY drift) shows what auto
would pick right now. `janus bench` stays deferred.

## Design decisions

- **Ranker** `routing/auto.py` (`plan_auto`): async, pure-input scoring; consumed by both
  `_handle` and the preview surface. Candidates come from a new
  `ProviderRegistry.auto_candidates()` (explicit per-prefix route models + catalog
  `default_models` for prefixes accepting any model), then filtered by key allowlist
  (pre-cut, via `key_access.model_allowed`, Janus semantics: absent list = all models),
  capability requirements, and priced-ness (`PricingRegistry.get`, prefix-stripped —
  OrcaRouter parity; unpriced models never win `cheapest` by costing $0).
- **Capabilities**: `routing/capabilities.py::detect_required_capabilities` gains
  `tool_use` (when `tools` present and `tool_choice.type != "none"`) and keeps `vision`
  from image parts. `json_mode` is **not** enforced: Janus's capability vocabulary has no
  such key and every model would be filtered out; revisit if a `json_mode` cap is added.
- **Scoring**: `cheapest` = blended 0.3·input + 0.7·output per MTok (lower is better);
  `quality` = error-rate composite from `storage/model_signals` (sufficient-sample stats)
  with operator overrides winning unconditionally; `fastest`/`balanced` need both signal
  axes (tps + ttft) — single-axis models drop to a bottom tier (eligible, never winner) so
  naive normalization cannot bias them over fully-scored models. Within the top tier,
  axes are min-max normalized across tier members; `balanced` = 0.5·quality + 0.5·(1−cost).
- **Sibling dedupe**: `canonical_model_base()` strips trailing version segments with 3+
  digits (`-001`, `-20250514`); `claude-opus-4-7` (1-digit revs) is NOT a version sibling.
  Best-ranked sibling per base survives; dedupe happens after scoring, before the top-N cut.
- **Try-order**: `_handle` resolves `auto` after the allowlist check and before budgets,
  then passes the chain to `resolve_attempts(model_chain=...)`, which reuses the combo
  expansion loop (accounts per model, cooldown filtering, rate-limit/quota/probed
  demotions, account strategies). Auto never bypasses those and never blocks; a model with
  no available accounts simply contributes no attempts and the chain cascades.
- **Trace**: `x-janus-resolved-model` already reports the served model. `request_logs`
  gains a nullable `resolved_model` column (idempotent migration) recorded on success;
  Request Logs shows requested vs resolved when they differ.
- **Settings**: `auto_routing_strategy` (validated enum, default `balanced`) in
  `SERVER_SETTING_DEFAULTS`; write path validator + settings-section exposure are
  automatic. The `routing` state section exposes `auto` = {strategy, top pick, sample
  ranking} via the same ranker (shape signature regenerated). RoutingPage gains a strategy
  select. Per-key override is explicitly deferred (issue marks it optional).
- **Overrides (#187 p2, PR B)**: `model_overrides` table (model PK, quality REAL, note,
  updated_at), dashboard CRUD routes, precedence inside the ranker over measured scores.
- **Preview (#187 p2, PR B)**: `GET /v1/quality/auto-preview` (API-key) + Routing page
  panel returning the ranker's full trace (model, tier, axes, score, excluded reasons) for
  the active strategy — same `plan_auto` call as the request path.

## Failure semantics

No candidates after filters → 404-style `auto_no_candidates` error naming the constraint
(strategy/caps/allowlist). Signals/pricing read failures log a warning and degrade to
`cheapest`-over-priced-candidates rather than failing the request.
