# Dashboard-first reliability audit

Date: 2026-11-02. Baseline: `098523d` (Janus v5.0.0).

This public report contains code findings, synthetic reproductions, and anonymized
operational observations. Deployment addresses, machine identities, private patches,
credentials, account identifiers, and raw request data are intentionally absent.
The investigation did not modify any deployed service or run billable probes.

## Product direction

Cloudline is the primary operator interface. A new operator should be able to
connect credentials, create client keys, test a model, inspect a failure, and find
the right recovery action in the dashboard. The CLI remains useful for deployment
bootstrap, headless servers, and automation.

The current README already says dashboard-first, and much of the implementation
supports it: six navigation hubs, shared section state, DB-backed mutations,
provider hot reload, inventory, budgets, and auto-routing preview. Remaining gaps
include terminal-based first-key creation, curl-only first-request guidance,
CLI-only benchmarks, and missing request-to-attempt diagnostics.

## Evidence and priorities

### Routing reliability

A large multi-account deployment can produce hundreds of attempts for one request
and take minutes to exhaust a route. An anonymized audit observed a maximum above
300 attempts and exhausted requests averaging about two minutes. Authentication,
payment, and permission failures dominated the upstream failure sample. These are
attempts, not distinct requests; cancellations must be counted separately.

Janus allows cooldown protection to be disabled. In that mode,
`FallbackHandler.mark_cooldown()` records nothing and `is_available()` bypasses
cooldown filtering. The dashboard previously showed zero cooling accounts without
explaining that protection was off. A healthy process and an empty cooldown list
therefore do not prove successful upstream routing.

Restore truthful diagnostics first; evaluate the cooldown setting through the
existing dashboard control before changing routing strategy. Turning cooldowns on
helps later requests, but a total dispatch deadline and attempt limit are still
needed to bound the first pass through a large failing pool. Distinguish transient
failures from credential recovery and confirmed quota/entitlement problems.

### Additional reproduced defects

- **Auto preview loses model IDs.** `RoutingPage.svelte` sends `auto.models`, a
  string array, through the object-only `firstList()` helper. A non-empty backend
  plan becomes the no-models empty state. A component test failed against the old
  implementation and passes with the corrected mapping.
- **Auto drops a healthy alternate.** Two providers serving `gpt-4o` are deduplicated
  by bare model base. With alpha cooled and beta available, a cheapest auto plan
  selects only alpha and fails with `AllAccountsCooledDown`; explicitly selecting
  beta works. Reproduced against an empty synthetic DB without upstream calls.
- **Malformed Retry-After bypasses cooldown.** `NaN` and negative numeric strings
  are accepted by `parse_retry_after`; the resulting state is immediately available.
  Ordinary invalid-string tests do not cover these float edge cases.
- **Cooldown deadlines are not monotonic.** A short late failure overwrites an
  existing longer delay. Overlapping account-wide and model locks also return the
  minimum lock expiry, even though both must expire before that account is ready.
  Both cases were reproduced with a frozen clock.
- **Dependency maintenance is due.** `npm audit` reports affected entries including
  devalue, source-map-js, undici, and Vite. Published advisories need an exposure
  review distinguishing static browser runtime from Node build/dev/test tooling;
  this is not evidence that each advisory affects the deployed Python server.

### Operator and deployment gaps

`request_outcomes`, `request_logs`, and `attempt_signals` have no shared request ID.
A reliable timeline needs explicit correlation rather than joins on timestamps.
It must work with body logging disabled and keep credentials out of ordinary state.

TLS-terminating proxies need documented trusted scheme/host handling. A browser
Origin with HTTPS and a backend request URL with HTTP will fail strict same-origin
checks when forwarding metadata is absent. Fix the deployment contract and tests;
do not globally remove scheme validation to make mutations work.

## Architecture constraints

- Keep `formats/` and `providers/` separated through `canonical/`.
- Keep dashboard and CLI/API controls on the same SQLite settings and reload path.
- Preserve leased provider snapshots across active requests and streams.
- Never replay after client-visible streaming output.
- Treat absent/stale capacity probes as neutral and retain model allowlists/budgets.
- Auto routing's default quality axis is observed reliability (`1 - error_rate`),
  not a benchmark of model intelligence. A preview without a concrete request is
  not a guarantee of eventual upstream success.

## OpenCodex references

The user-selected reference is [lidge-jun/opencodex](https://github.com/lidge-jun/opencodex),
inspected at `684ad75d254ae96a9971387d09ae428b96ada9c2` (MIT). No code was copied.

- [Cooldown classification](https://github.com/lidge-jun/opencodex/blob/684ad75d254ae96a9971387d09ae428b96ada9c2/src/codex/routing/cooldown-math.ts)
  distinguishes credential, entitlement, quota, transient, and caller failures,
  including stale credential generations and different reset signals.
- [Key cooldown state](https://github.com/lidge-jun/opencodex/blob/684ad75d254ae96a9971387d09ae428b96ada9c2/src/providers/api-key-cooldowns.ts)
  preserves a later expiry when another failure arrives.
- [Attempt attribution](https://github.com/lidge-jun/opencodex/blob/684ad75d254ae96a9971387d09ae428b96ada9c2/src/server/request-log-account-rotation.ts)
  keeps each send attached to the account actually used.
- [Combo behavior](https://opencodex.me/guides/combos/) describes failure classes,
  tool-turn affinity, and explicit metered fallback policy.
- [Streaming architecture](https://opencodex.me/reference/architecture/) discusses
  heartbeats and stall deadlines. Evaluate these with cancellation and bounded
  time-to-first-output rather than adding unbounded retries.

## Issue index

The work is coordinated in [tracker #278](https://github.com/amanverasia/Janus/issues/278).

Each issue includes evidence, scope, acceptance criteria, and related closed work.
Prioritize diagnostic fixes, routing correctness, and bounded dispatch; then build
request timelines, recovery actions, browser testing, and secure bootstrap.

- [#266: fix(dashboard): expose disabled cooldown protection and log cooldown rejections](https://github.com/amanverasia/Janus/issues/266)
- [#267: feat(routing): bound total dispatch duration and fallback attempts](https://github.com/amanverasia/Janus/issues/267)
- [#268: feat(dashboard): correlate requests with upstream attempts and recovery actions](https://github.com/amanverasia/Janus/issues/268)
- [#269: feat(inventory): provide classified account recovery and safe re-entry into routing](https://github.com/amanverasia/Janus/issues/269)
- [#270: feat(dashboard): add a bounded authenticated model test workflow](https://github.com/amanverasia/Janus/issues/270)
- [#271: design(dashboard): secure first-administrator bootstrap without routine CLI setup](https://github.com/amanverasia/Janus/issues/271)
- [#272: fix(deployment): document trusted TLS proxy origins and test dashboard mutations](https://github.com/amanverasia/Janus/issues/272)
- [#273: fix(dashboard): preserve string model IDs in the auto-routing preview](https://github.com/amanverasia/Janus/issues/273)
- [#274: fix(routing): retain healthy provider alternatives during auto-model deduplication](https://github.com/amanverasia/Janus/issues/274)
- [#275: fix(routing): reject non-finite and negative Retry-After values](https://github.com/amanverasia/Janus/issues/275)
- [#276: fix(routing): preserve cooldown expiry and calculate retry time across overlapping scopes](https://github.com/amanverasia/Janus/issues/276)
- [#277: chore(dashboard): triage and update dependencies flagged by npm audit](https://github.com/amanverasia/Janus/issues/277)

## Initial implementation

The accompanying changes record all-cooled 503s in Request Logs when logging is
enabled; show bounded escaped failure reasons and correct stream/resolved-model
details; warn when cooldown protection is disabled; and preserve string model IDs
in the auto preview. The runtime dashboard bundle is rebuilt. Agent instructions
are refreshed and synchronized; both files are excluded from the sdist.

The other issues remain follow-up work. No live settings, credentials, deployment
configuration, or routing retry limits are changed by this PR.

## Validation

- 147 focused Python API, logging, cooldown, storage, dashboard contract, and size
  tests passed. Ruff, mypy (166 files), migration smoke, and strict docs passed.
- 99 dashboard component tests passed after the preview fix, with zero Svelte
  errors/warnings, formatting checks, and a rebuilt production bundle.
- The synthetic browser regression passed all 20 routes, navigation, redirects,
  preview/export, filtering, and reversible CRUD before the final preview mapping
  correction. Its pricing-pagination scenario skipped for insufficient fixture
  rows; component pagination coverage ran. The final mapping has component coverage.
- Wheel and sdist packaging include the final dashboard bundle and exclude both
  agent instruction files. Generated bundle parity and agent-file identity pass.

Local Python checks used an existing 3.14.6 environment; the full Python suite and
supported 3.11/3.12 matrix remain CI gates. No production-data fixture or deployment
identifier is required to reproduce the code findings.
