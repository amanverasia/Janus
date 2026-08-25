# Dashboard frontend assets

The dashboard serves these committed assets locally so normal operation never depends on a public
CDN. Versions are pinned to HTMX 2.0.4, Chart.js 4.4.1, D3 7.9.0, d3-sankey 0.12.3, and Tailwind
CSS 3.4.17.

From the repository root, refresh downloads and rebuild the Tailwind bundle with:

```bash
python scripts/update_dashboard_assets.py
```

The updater downloads exact-version assets, verifies their source SHA-256 digests, compiles all
dashboard templates with the pinned Tailwind standalone compiler, and writes `manifest.json` with
the committed files' checksums and Tailwind input digests. On a platform other than Linux x86-64,
download the Tailwind CSS 3.4.17 standalone CLI and pass it through `--tailwind-cli PATH`; the
updater rejects a different compiler version.

Review the generated diff and run `python -m pytest tests/unit/dashboard/test_static_assets.py`
before committing an update. Third-party license texts are retained under `licenses/`.
