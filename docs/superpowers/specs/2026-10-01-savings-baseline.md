# Savings vs baseline model (#237)

Date: 2026-10-01. Status: implemented per the user-approved #239 cluster order. Issue: #237.

## Goal

Show what routed traffic would have cost on a baseline model (default `gpt-4o`), how much
routing saved, and a per-model breakdown — on the Analytics page, a "Saved today" tile on
Home, and an optional public `GET /v1/analytics/savings`.

## Decisions

- Counterfactual cost re-prices the four recorded token streams through the existing
  `compute_cost()` (cache tokens are subsets of `input_tokens` there); no duplicate math.
- One grouped SQL aggregate per window (GROUP BY model over non-subscription rows) plus
  subscription/unpriced counters from the same scan; Python applies pricing per distinct
  model, so work scales with distinct models, not rows.
- Subscription-provider rows (`SUBSCRIPTION_API_TYPES` via `_not_subscription_provider_clause`)
  and unpriced rows (cost `NULL`/`0`) are excluded from savings and reported as separate
  counters — they never count as 100% savings.
- `analytics` section (value-pinned) gains `data.savings`; `overview` (value-pinned) gains
  `data.savings_today` computed on the same calendar-day bounds as budget "today"
  (`current_reporting_day`). Rolling windows are labeled "Last N days".
- Baseline default lives in a new `analytics_savings_baseline` setting (default `gpt-4o`,
  non-empty, ≤100 chars); the `baseline` query param is validated against the
  `PricingRegistry` (422 for unknown/unpriced ids); an unpriced stored default reports
  `baseline_priced: false` instead of erroring.
- Token-saver attribution is out of scope (issue marks it optional); the figure is labeled
  routing/model-choice savings.

## Payload

```json
{
  "baseline": "gpt-4o",
  "baseline_priced": true,
  "actual_cost": 12.34,
  "baseline_cost": 45.67,
  "savings": 33.33,
  "savings_pct": 72.9,
  "requests": 210,
  "excluded": {"subscription_requests": 14, "unpriced_requests": 3},
  "by_model": [
    {"model": "deepseek-chat", "requests": 100, "actual_cost": 1.2,
     "baseline_cost": 9.9, "savings": 8.7}
  ]
}
```

`by_model` is capped at 25 rows sorted by savings desc; `overview.savings_today` carries the
headline fields only (no `by_model`). Windows: analytics uses `?days=` (1–365, default 30,
UTC-relative like the existing spend summary); the Home tile uses the configured
reporting-timezone calendar day.

## Surfaces

- `GET /dashboard/api/v2/state/analytics?baseline=&days=` → `data.savings`.
- `GET /dashboard/api/v2/state/overview` → `data.savings_today`.
- `GET /v1/analytics/savings?baseline=&days=` (API-key auth, same validation and payload).
- Home tile "Saved today" links context in the Analytics page; Analytics gets a savings
  panel (headline StatCards + baseline select + per-model table).

## Testing

Unit: counterfactual math (reuses compute_cost cases incl. cache subsets), grouped-totals
query (subscription/unpriced exclusion, window bounds, by_model cap/order), settings
resolver/validator. Integration: section payloads, baseline 422s, today-window semantics
(fixed `now`), public endpoint auth + days clamp, settings roundtrip. Contract fixtures
(`overview.json`, `analytics.json`, `settings.json`) regenerated and `contracts.ts` updated
in the same change. Vitest for the Overview tile and the Analytics savings panel.
