# Vendored front-end assets

Tempa's Python side is standard-library only, and the dashboard page itself is hand-written
with no build step. This folder is the one exception: third-party front-end code that is too
large to hand-roll and too useful to skip, committed here verbatim so the dashboard keeps
working with no network access and no package manager.

Nothing in this folder is inlined into the page. `assets/js/*.js` is concatenated into the
single inline `<script>` (see `dashboard_assets.JS_PARTS`); files here are served from their
own route instead and fetched lazily, only when something on the page actually needs them.

## mermaid 11.16.1

Renders the ```mermaid fences in a spec as diagrams (`assets/js/12-mermaid.js`, served by
`dashboard_server._serve_mermaid` at `/assets/mermaid.min.js`).

| | |
|---|---|
| Version | 11.16.1 |
| License | MIT — see `mermaid.LICENSE` |
| Bundle | https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.min.js |
| License text | https://raw.githubusercontent.com/mermaid-js/mermaid/develop/LICENSE |
| sha256 | `18327bef70d96fb505fe7287d9f6a7362ebf07ff6576ddfaffb1a06f3e1a2954` |
| Size | 3,566,058 bytes |

**It must be the UMD build (`dist/mermaid.min.js`), never the ESM one**
(`dist/mermaid.esm.min.mjs`). The ESM file is a ~30 KB loader that `import()`s dozens of
sibling chunks on demand: it would work while the CDN is reachable and break the first time
someone opens a diagram offline. The UMD bundle is self-contained — no dynamic `import()`, no
`sourceMappingURL` — which is what `tests/test_dashboard_assets.py` pins down.

### Upgrading

1. Download the new bundle and the license text from the URLs above (bump the version in
   both the URL and this table).
2. Record the new sha256:
   `python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('src/assets/vendor/mermaid.min.js').read_bytes()).hexdigest())"`
3. Bump `MERMAID_VERSION` in `src/dashboard_assets.py` — it is the `?v=` cache-buster the page
   requests, so a stale value serves the old bundle out of the browser cache.
4. Run `pytest tests/test_dashboard_assets.py tests/test_dashboard_server_routes.py`, then
   open a spec with a diagram in the dashboard and confirm DevTools shows no request to any
   host other than Tempa's own server.
