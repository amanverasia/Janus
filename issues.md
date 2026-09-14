# Janus full-stack audit — 2026-08-26

This is the living report from a production-data audit of Janus 3.1.0. The audit used a private
copy of `janus-data/janus.db` in an isolated Docker container bound to `127.0.0.1:20129`. Inventory
and pricing schedulers were disabled so the source data was not mutated. No credentials, request
bodies, account IDs, or decrypted secrets are included here or in GitHub.

## Executive summary

- The SQLite database passes `PRAGMA quick_check` and the application/container start cleanly.
- All dashboard routes and state endpoints returned structured responses; no new DOM-XSS or CDN
  dependency regression was found.
- One P0 security defect, nine P1 backend correctness/resilience defects, and multiple dashboard,
  operations, test, and quality-of-life gaps were confirmed.
- The most visible latency is real: warm small dashboard tabs spend roughly 0.52–0.58 seconds in
  global alert collection, analytics takes roughly 1.5–1.9 seconds, and the first dashboard state
  request repeats startup work and takes roughly 5.5–6.2 seconds.
- `GET /v1/models` takes roughly 4.3–4.5 seconds on every call because discovery materializes more
  than 100,000 account targets for a roughly 1,100-row public catalog.
- GitHub issues [#100](https://github.com/amanverasia/Janus/issues/100) through
  [#124](https://github.com/amanverasia/Janus/issues/124) track every actionable finding and the
  quality-of-life backlog.

## Implemented in this audit worktree

The following fixes are present in the current worktree and were verified against a copied
production-size database. They are intentionally still open on GitHub until they are committed
and merged:

- #107: remote image prefetch now rejects private, link-local, metadata, reserved, and credential-bearing URLs; validates every redirect; pins validated DNS addresses while preserving Host/SNI; ignores the private-URL development override for user images; rejects compressed encodings before body iteration; and enforces 20 MiB per-image plus cumulative request budgets.
- #102: model discovery uses indexed route checks instead of materializing every account target, emits unique effective model IDs, preserves combo order, and prevents broken or disallowed combos from hiding physical models.
- #106: dashboard startup is single-flight, alerts are cached/coalesced and invalidated after mutations, and the SPA keeps per-view state while revalidating in the background with page-shaped skeletons during the initial load.
- #100: Request Logs now maps the backend's timestamp, status, and duration fields correctly.
- #109: inventory attention counts include legacy unhealthy states and archived keys are excluded from visible totals.

Issues #101, #103, #104, and #105 remain deliberately tracked: this audit identified them, but they
need separate behavioral changes and dedicated upstream/streaming fixtures rather than a partial fix.

## Environment and evidence

| Item | Result |
| --- | --- |
| Live database | 163 MiB; 154,313 usage rows; 855 upstream keys; 22 provider rows |
| Data directory | About 1.3 GiB including seven full-size database backups |
| Database integrity | `PRAGMA quick_check` returned `ok`; no foreign-key violations |
| Docker | Janus 3.1.0 on Python 3.11.16; `/v1/health` returned 200 |
| Dashboard routes | Overview, Usage, Analytics, Leaderboard, Request Logs, Inventory, Providers, Models, Combos, Routing, Savers, Budgets, Keys, Tools, Pricing, and Settings loaded |
| Static/security | Local bundled assets; escaped Svelte interpolation; no dynamic `innerHTML`/`{@html}` regression |
| Existing audit | Closed issues #88–#94 were rechecked; their code fixes remain present |

The live database still contains 43,526 historical no-op inventory history rows created before
#88 was fixed. Cleanup must remain an explicit, backed-up operator action; it must not be hidden in
a startup migration.

## GitHub issue index

### Security and gateway correctness

| Priority | Finding | GitHub |
| --- | --- | --- |
| P0 | Remote image prefetch permits authenticated SSRF and buffers oversized bodies before enforcing its limit | [#107](https://github.com/amanverasia/Janus/issues/107) |
| P1 | Public model discovery is quadratic and advertises duplicate/shadowed routes | [#102](https://github.com/amanverasia/Janus/issues/102) |
| P1 | Analytics success rate is derived from success-only usage records | [#103](https://github.com/amanverasia/Janus/issues/103) |
| P1 | Streaming/transport failures and HTTP-200 error envelopes bypass consistent fallback/cooldown accounting | [#104](https://github.com/amanverasia/Janus/issues/104) |
| P1 | Cache-token semantics cause missing or double-counted cost across adapters | [#105](https://github.com/amanverasia/Janus/issues/105) |
| P2 | RPD and subscription request counters lose failed attempts on reload/restart | [#101](https://github.com/amanverasia/Janus/issues/101) |
| P2 | Malformed gateway JSON can become an internal error instead of structured 400/422 | [#108](https://github.com/amanverasia/Janus/issues/108) |
| P2 | Provider/model reload processes raw per-key duplicate discoveries | [#114](https://github.com/amanverasia/Janus/issues/114) |

### Dashboard correctness and performance

| Priority | Finding | GitHub |
| --- | --- | --- |
| P1 | First request repeats startup; every tab pays alert-query latency; navigation blanks content instead of showing cached state/skeletons | [#106](https://github.com/amanverasia/Janus/issues/106) |
| P1 | Request Logs renders every timestamp/status/duration as `Never / — / 0 ms` | [#100](https://github.com/amanverasia/Janus/issues/100) |
| P1 | Large dashboard state is uncompressed and overfetched | [#111](https://github.com/amanverasia/Janus/issues/111) |
| P1 | Generic JSON contracts and no component/E2E suite allow API/UI drift | [#110](https://github.com/amanverasia/Janus/issues/110) |
| P2 | Inventory attention totals omit some unhealthy states; add/import and filters have state inconsistencies | [#109](https://github.com/amanverasia/Janus/issues/109) |
| P2 | Provider-to-model links reload the SPA and model-provider deep links are ignored | [#113](https://github.com/amanverasia/Janus/issues/113) |
| P2 | Usage fetches the initial live snapshot three times | [#115](https://github.com/amanverasia/Janus/issues/115) |
| P2 | Settings controls remain visually changed after failed saves | [#117](https://github.com/amanverasia/Janus/issues/117) |
| P2 | Dashboard requests have no deadline and hashed assets lack immutable caching | [#121](https://github.com/amanverasia/Janus/issues/121) |
| P2 | Bare SQLite UTC timestamps are parsed as browser-local time | [#120](https://github.com/amanverasia/Janus/issues/120) |

### Provider operations

| Priority | Finding | GitHub |
| --- | --- | --- |
| P1 | Connection tests are unsupported for displayed executors, use an unrepresentative first model/credential, and block on DNS safety checks | [#112](https://github.com/amanverasia/Janus/issues/112) |
| P2 | Dashboard health/provider-health/identity indicators are hardcoded rather than state-backed | [#119](https://github.com/amanverasia/Janus/issues/119) |

The built-in one-token probe was run once against each of the 21 enabled provider rows in the
isolated copy. Eight returned an upstream success, ten returned an upstream 4xx response, and three
executor types were rejected as unsupported by the test endpoint. These results are diagnostic,
not a definitive provider-health score: #112 documents why the current probe can report false
negatives when the first configured model does not belong to the selected inventory credential.

### Operations, packaging, and quality of life

| Priority | Finding | GitHub |
| --- | --- | --- |
| P2 | Request-log export and backup accumulation need bounded-memory/retention tooling | [#124](https://github.com/amanverasia/Janus/issues/124) |
| P2 | Docker lacks a health check and performs avoidable root/chown work | [#118](https://github.com/amanverasia/Janus/issues/118) |
| P2 | The source distribution includes internal workspace and planning artifacts | [#123](https://github.com/amanverasia/Janus/issues/123) |
| P2 | CI lacks supported-version, coverage, migration/browser, artifact, and publish-gating checks | [#122](https://github.com/amanverasia/Janus/issues/122) |
| P3 | Accessibility and operator QoL pass: type sizes, empty action columns, command-palette keys, structured errors, runnable Tools examples, Leaderboard range | [#116](https://github.com/amanverasia/Janus/issues/116) |

## Performance measurements

### Pre-fix baseline

### Dashboard state endpoints

| State | Time | Response size |
| --- | ---: | ---: |
| Overview | 0.90 s | 48 KB |
| Usage | 0.77 s | 46 KB |
| Analytics | 1.51–1.85 s | 51 KB |
| Inventory | 0.64 s | 34 KB |
| Models | 0.66–0.72 s | 806–877 KB |
| Providers | 0.61 s | 121 KB |
| Routing | 0.64–0.69 s | 153–163 KB |
| Pricing | 1.26–1.30 s | 699–748 KB |
| Small states | 0.52–0.58 s | 1–10 KB |

The global unpriced-model alert query alone took about 0.63 seconds on the real-size usage table.
Models, Pricing, and Routing payloads gzip to a small fraction of their wire size, but the server
does not currently compress them.

### Post-fix comparison

On a fresh image using the same copied database, the warm dashboard timings were approximately:

| State | Post-fix time | Result |
| --- | ---: | --- |
| Overview | 0.39 s warm (1.00 s first) | Cached state remains visible during revalidation |
| Analytics | 0.24–0.28 s | Alert work is cached and parallelized |
| Request Logs | 0.012 s | Correct timestamps/statuses/latencies rendered |
| Inventory | 0.18 s | `needs_attention` is explicit; archived rows excluded |
| Public `/v1/models` | 0.007 s | 1,134 entries, 1,134 unique IDs |

The final image also passed a browser smoke loop across all 16 dashboard views; all routes returned
HTTP 200 and rendered their expected page content.

### Pre-fix public model catalog

The endpoint returned 1,135 entries but only 1,134 unique IDs because a combo intentionally shadows
a physical model. Registry analysis showed one large provider prefix with 244 active accounts and
431 discovered models; the current discovery code materializes approximately 105,000 targets just
to answer a boolean routability question.

## Automated quality gates

Passed after implementation:

- Ruff lint and format checks.
- Strict mypy over 138 source files.
- MkDocs strict build.
- Dashboard Prettier, Svelte check, Vite build, and committed-bundle verification.
- Wheel and sdist build; the wheel includes the dashboard runtime assets.
- Focused prefetch/URL-guard, registry/model-catalog, inventory, dashboard, and Ollama regressions.

The complete post-fix suite collected 1,364 tests and passed all 1,364 in 7m15s on Python 3.11.
There were 14 non-failing Python 3.14/aiosqlite event-loop teardown warnings. The repository
`.venv` remains stale in a few generated console-script paths; invoke tools through
`.venv/bin/python -m ...` as required by `AGENTS.md`, or recreate the environment.

## Follow-up policy

- Keep GitHub issues open until their fixes are committed/merged and verified against a fresh
  Docker image.
- Never include provider credentials, request bodies, account identifiers, or raw production HTML
  in issue comments or fixtures.
- Use copied or synthetic databases for destructive cleanup, mutation, and browser screenshots.
- Prioritize #107, #106, #102, and #100 before broader QoL work.
