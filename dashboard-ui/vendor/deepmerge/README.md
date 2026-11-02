# Patched build dependency: deepmerge

This private package replaces the Svelte Vite plugin's build-time `deepmerge`
dependency. It is not published to npm or included as a Node production runtime.

## Provenance

- Upstream: [TehShrike/deepmerge](https://github.com/TehShrike/deepmerge),
  release 4.3.1, commit `5b87756a5671635679001cbac72aa42f23472c81`.
- Source: the self-contained `dist/cjs.js` from the official npm tarball
  `https://registry.npmjs.org/deepmerge/-/deepmerge-4.3.1.tgz`.
- Original file SHA-256:
  `2e5160f84a1141bd927264afe4d15766f1e6cf07daef182b37ff53fea121de7d`.
- Original tarball integrity:
  `sha512-3sUqbMEc77XqpdNO7FRyRog+eW3ph+GYCbj+rK+uYyRMuwsVy0rMiVtPn+QJlKFvWP/1PYpapqYn0Me2knFn+A==`.
- License: MIT, retained in [LICENSE](LICENSE).

The bundled implementation is retained, with comments removed and formatting
normalized. The security change filters the reserved string keys `__proto__`,
`constructor`, and `prototype` in `getKeys`. Both the target-copy and source-merge
loops use that function, including recursive clones. Symbols and the existing
array/custom merge behavior are preserved.

This fixes [GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4):
target-dependent safety checks alone let a returned object inherit an attacker
prototype when the target is null-prototype, primitive, or already owns an unsafe
key. This patch does not trust those target properties as evidence of safety.

## Resolution and maintenance

The root `deepmerge` development dependency points here, and npm's `$deepmerge`
override makes every transitive consumer resolve to that same reviewed source.
The name `@janus/deepmerge` and version `4.3.1-janus.1` identify this local patch;
it is not represented as an upstream patched release.

Run `node --test scripts/deepmerge-security.test.mjs` from `dashboard-ui/`.
The tests resolve through the actual Svelte plugin, prove the reported attack
cases are blocked, and exercise normal configuration merging. They failed
against the original upstream release before the patch.

`npm audit` does not assess private local source as a registry package. A clean
audit therefore supplements these source-level security tests; it does not certify
the patch by itself. No audit exclusion or severity suppression is configured.

Remove this package and the override when the plugin drops `deepmerge` or upstream
publishes a release with equivalent fixes. Re-run the security tests against the
replacement, then update the explicit resolution test and this provenance note.
