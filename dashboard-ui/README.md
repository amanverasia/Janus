# Janus dashboard UI

The dashboard is authored as a static SvelteKit and TypeScript application. Janus does not
need Node.js in production: FastAPI serves the committed bundle from
`src/janus/dashboard/static/app/`.

## Development

Use Node.js 22.17 or newer. The dashboard build uses SvelteKit 3, Vite 8 and
TypeScript 6; production remains a static bundle served by Python.

```bash
npm ci
npm run format:check
npm run check
npm run dev
```

The development server proxies `/dashboard/api` to Janus on port `20128`. The application
route base is `/dashboard/ui`; production assets are emitted under
`/dashboard/static/app/`.

## Build and verify

From the repository root:

```bash
.venv/bin/python scripts/build_dashboard_ui.py
.venv/bin/python scripts/build_dashboard_ui.py --check
```

The build script installs the locked dependencies, verifies formatting, runs Svelte
diagnostics, creates the static build, and atomically replaces only the committed dashboard
application bundle.

It also rejects registry advisories at every severity and runs the build dependency's
prototype-poisoning regression tests before the component suite. The Svelte plugin's
`deepmerge` dependency resolves to the reviewed local patch in `vendor/deepmerge`;
see its provenance and maintenance instructions there.

## Constraints

- Keep runtime assets local; the production dashboard must not depend on public CDNs.
- Treat API and database values as untrusted and render them through Svelte text bindings.
- Never include decrypted credentials in page state. Secrets require an explicit
  authenticated, non-cacheable reveal or creation response.
- Preserve `/dashboard/ui` deep-link behavior when adding modules.
