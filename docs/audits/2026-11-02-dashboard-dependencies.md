# Dashboard dependency review

Date: 2026-11-02. Issue: [#277](https://github.com/amanverasia/Janus/issues/277).
Baseline lockfile: `ce87959`. Review commands, from `dashboard-ui/`:

```bash
npm ci
npm audit --json
npm ls devalue source-map-js undici vite vitest cookie deepmerge @sveltejs/kit
```

The baseline audit reported 12 affected package entries: four high, two moderate,
and six low. The compatible update in PR #283 removed the high/moderate entries.
The subsequent full remediation below removes the remaining six entries: the fresh
registry audit reports zero vulnerabilities at every severity. These counts include
inherited findings rather than twelve independent vulnerabilities. Registry results
can change; the local patched dependency is verified separately by security tests.

## High and moderate findings remediated

| Package | Dependency path | Locked update | Exposure in Janus |
| --- | --- | --- | --- |
| devalue | SvelteKit and Svelte → devalue | 5.9.1 → 5.9.4 | Framework client serialization/parsing code can enter the generated browser bundle; also used during framework builds. Janus's dashboard API uses ordinary JSON. Treat this as a potential browser dependency, even though Janus has no Node SSR server. |
| source-map-js | Vite → PostCSS; jsdom → css-tree | 1.2.1 → 1.2.2 | Node build/test processing of source maps. Neither Node source-map parsing nor these development servers run in the Python production image. |
| undici | jsdom → undici | 7.29.1 → 7.30.1 | Node HTTP client in component tests. Not the Python gateway's upstream client, which uses httpx. Not shipped as a Node runtime dependency. |
| Vite | Direct; SvelteKit, Vite plugin, Vitest → Vite | 7.3.6 → 7.3.7 | Development server/editor/middleware exposure. Production serves committed static assets through Python, without a Vite listener. Keep the development server private. |
| Vitest / @vitest/mocker | Direct Vitest → mocker | 5.0.0 → 5.0.5 | Moderate mock-registration/traversal advisories affect the test tooling. Janus runs `vitest run`, not a production Vitest server. |

The exposure assessment above is inferred from Janus's Dockerfile, static adapter,
`ssr=false` layout, build scripts, and dependency paths. It is not a claim that a
package marked `devDependency` can never reach browser output.

Published advisories include [devalue](https://github.com/advisories/GHSA-4q55-j62x-fr9h),
[source-map-js](https://github.com/advisories/GHSA-68fv-2mgg-jv7q),
[undici](https://github.com/advisories/GHSA-2m5v-pch6-74hx),
[Vite](https://github.com/advisories/GHSA-9jrq-w75r-8gcw), and
[Vitest mocker](https://github.com/advisories/GHSA-qxg6-89mg-rqv8).
The audit groups additional advisories under the same package entries; the updates
remove all high and moderate entries in the fresh audit, not just these examples.

The initial updates stayed within existing major versions and dependency ranges. Vite and Vitest's
direct minimums now select patched releases. No forced audit fix, transitive major
override, or framework migration was used. The production bundle is regenerated
from the new lockfile and remains locally served in the wheel and Docker image.

## Initial low findings assessment (superseded by full remediation)

Six affected entries remained after PR #283: `@sveltejs/adapter-static`, `@sveltejs/kit`,
`@sveltejs/vite-plugin-svelte`, `@sveltejs/vite-plugin-svelte-inspector`, `cookie`,
and `deepmerge`. The adapter/plugin/inspector entries inherit findings from their
dependency graph; they are not six distinct browser vulnerabilities.

| Originating advisory | Current path and version | Assessment and follow-up |
| --- | --- | --- |
| [Enhanced form open redirect](https://github.com/sveltejs/kit/security/advisories/GHSA-pfhg-q3qv-9q6r) | SvelteKit 2.70.3 | Requires enhanced SvelteKit form actions. Dashboard forms use Janus fetch handlers, with no `use:enhance` or SvelteKit server actions. Fixed upstream in SvelteKit 3; revisit before introducing enhanced forms. |
| [Prerender cache-control XSS](https://github.com/sveltejs/kit/security/advisories/GHSA-27mw-82g8-phjj) | SvelteKit 2.70.3 | Requires untrusted cache-control passed through `setHeaders` while prerendering. Dashboard source has no `setHeaders` or remote content-derived prerender headers. Fixed upstream in SvelteKit 3; revisit if build-time fetching is added. |
| [Cookie field validation](https://github.com/jshttp/cookie/security/advisories/GHSA-pxg6-pf52-xh8x) | SvelteKit → cookie 0.6.0 | Node cookie serialization is not used by the Python login/session routes. They use Starlette and fixed cookie names/paths. SvelteKit's current range retains this version; do not force a transitive replacement without compatibility review. |
| [Prototype poisoning](https://github.com/advisories/GHSA-gcqx-w48m-w2h4) | Vite plugin → deepmerge 4.3.1 | Build configuration merging, not a dashboard request parser. Only trusted checked-in configuration is merged. No patched deepmerge version is published at review time. Revisit if untrusted build configuration is accepted. |

These findings were initially accepted to keep the compatible maintenance batch
separate from the framework migration. They are now remediated as described below.
Do not claim a clean audit from `npm audit --omit=dev`: the entire frontend toolchain
is listed under development dependencies even though it produces browser runtime code.

## Full remediation

The user requested removal of every remaining advisory, including the required
framework migration. The updated toolchain uses SvelteKit 3.1.1, adapter-static 4.0.1,
Vite plugin 7.3.1, Vite 8.3.5, Svelte 5.57.3, and TypeScript 6.0.3. SvelteKit now
uses cookie 2.0.1; the obsolete inspector dependency is no longer present.

The migration follows the [official SvelteKit 3 guide](https://svelte.dev/docs/kit/migrating-to-sveltekit-3):
shared configuration is passed to the Vite plugin, library imports use explicit
`#lib` package imports and file extensions, and TypeScript extends `$app/tsconfig`.
Both production and component tests use the same adapter/base/version settings.
Builds require Node 22.17 or newer; the deployed Python image remains Node-free.

The latest Vite plugin still depends on deepmerge 4.3.1, and upstream has no patched
release. A private local package, `@janus/deepmerge` version `4.3.1-janus.1`, retains
the MIT-licensed bundled implementation and filters `__proto__`, `constructor`, and
`prototype` from both target and source key enumeration. This covers recursive
cloning and the null-prototype/primitive/own-key bypasses in the original guard.
The root local development dependency and npm `$deepmerge` override make the plugin
resolve to this exact source. Provenance, original integrity/hash, the narrow patch,
license, and removal criteria are recorded in `dashboard-ui/vendor/deepmerge/README.md`.

Seven Node security/compatibility tests run through the plugin's actual dependency
resolution. Four poisoning regressions failed against the original implementation;
the patched implementation passes all seven, including configuration array overrides,
symbol properties, functions, special values, and custom merge behavior. No registry
advisory is ignored or filtered. Because npm does not assess private local source,
zero registry findings alone cannot attest to this patch; its source review and
security tests are separate required checks.

The normal dashboard build now runs `npm audit --audit-level=low`, the security tests,
and all component tests. A newly published advisory at any severity fails that gate.
Remove the local fork when upstream supplies an equivalently fixed dependency, after
re-running the security regressions against the replacement.

## Verification

Use the committed lockfile and the normal repository gates:

```bash
.venv/bin/python scripts/build_dashboard_ui.py --check
.venv/bin/python scripts/browser_regression.py
.venv/bin/python -m build
```

The bundle check performs a fresh install, formatting, Svelte checks, component tests,
and a byte-for-byte build comparison. Browser regression requires a running isolated
synthetic gateway and test key; never point it at a production database. Verify that
the wheel contains the rebuilt dashboard. The supported Python 3.11/3.12 matrix
remains a required CI gate.
