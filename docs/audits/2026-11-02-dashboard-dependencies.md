# Dashboard dependency review

Date: 2026-11-02. Issue: [#277](https://github.com/amanverasia/Janus/issues/277).
Baseline lockfile: `ce87959`. Review commands, from `dashboard-ui/`:

```bash
npm ci
npm audit --json
npm ls devalue source-map-js undici vite vitest cookie deepmerge @sveltejs/kit
```

The baseline audit reports 12 affected package entries: four high, two moderate,
and six low. The updated lockfile reports six low entries and no high, moderate,
or critical entries. These counts describe package entries, including inherited
findings, rather than twelve independent vulnerabilities. Registry results can change.

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

Updates stay within existing major versions and dependency ranges. Vite and Vitest's
direct minimums now select patched releases. No forced audit fix, transitive major
override, or framework migration was used. The production bundle is regenerated
from the new lockfile and remains locally served in the wheel and Docker image.

## Remaining low findings accepted for this update

Six affected entries remain: `@sveltejs/adapter-static`, `@sveltejs/kit`,
`@sveltejs/vite-plugin-svelte`, `@sveltejs/vite-plugin-svelte-inspector`, `cookie`,
and `deepmerge`. The adapter/plugin/inspector entries inherit findings from their
dependency graph; they are not six distinct browser vulnerabilities.

| Originating advisory | Current path and version | Assessment and follow-up |
| --- | --- | --- |
| [Enhanced form open redirect](https://github.com/sveltejs/kit/security/advisories/GHSA-pfhg-q3qv-9q6r) | SvelteKit 2.70.3 | Requires enhanced SvelteKit form actions. Dashboard forms use Janus fetch handlers, with no `use:enhance` or SvelteKit server actions. Fixed upstream in SvelteKit 3; revisit before introducing enhanced forms. |
| [Prerender cache-control XSS](https://github.com/sveltejs/kit/security/advisories/GHSA-27mw-82g8-phjj) | SvelteKit 2.70.3 | Requires untrusted cache-control passed through `setHeaders` while prerendering. Dashboard source has no `setHeaders` or remote content-derived prerender headers. Fixed upstream in SvelteKit 3; revisit if build-time fetching is added. |
| [Cookie field validation](https://github.com/jshttp/cookie/security/advisories/GHSA-pxg6-pf52-xh8x) | SvelteKit → cookie 0.6.0 | Node cookie serialization is not used by the Python login/session routes. They use Starlette and fixed cookie names/paths. SvelteKit's current range retains this version; do not force a transitive replacement without compatibility review. |
| [Prototype poisoning](https://github.com/advisories/GHSA-gcqx-w48m-w2h4) | Vite plugin → deepmerge 4.3.1 | Build configuration merging, not a dashboard request parser. Only trusted checked-in configuration is merged. No patched deepmerge version is published at review time. Revisit if untrusted build configuration is accepted. |

The audit's proposed resolution includes SvelteKit 3 and adapter-static 4, which is
a separate framework migration. Accept these low findings for this compatible
maintenance batch with the constraints above. Do not claim a clean audit from
`npm audit --omit=dev`: the project lists its entire frontend toolchain under
development dependencies even though the build produces browser runtime code.

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
