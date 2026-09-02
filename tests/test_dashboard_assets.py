"""Safety net for dashboard_assets.py — how the single self-contained page is assembled.

The dashboard ships as ONE document: dashboard.css and dashboard.js are inlined into
dashboard.html, and render_page() then substitutes the per-request data placeholders. Two
things can break silently there and neither shows up in any other test: a placeholder that
stops matching (the page would quietly render with `null` data), and an asset that stops
being inlined (the page would try to fetch it and fail offline).

These tests assert the composition itself rather than a byte-for-byte snapshot, so they
keep holding when the UI legitimately changes — including when the JS is split into
several source files that get concatenated back into one inline script.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

import dashboard_assets

# Every placeholder render_page() fills in. Each must (a) still exist in the template and
# (b) be gone from the rendered page — a renamed marker fails loudly here instead of
# shipping a dashboard whose state is `null`.
DATA_PLACEHOLDERS = [
    "/*__SPEC_TREE__*/null",
    "/*__CLARIFY_UNANSWERED__*/null",
    "/*__CLARIFY_ANSWERED__*/null",
    "/*__PRD_NAME__*/null",
    "/*__INITIAL_VIEW__*/null",
    "/*__WORKSPACE_INITIALIZED__*/null",
    "/*__WORKSPACE_ROOT__*/null",
    "/*__WORKSPACE_CAN_CLOSE__*/null",
    "/*__WORKSPACE_RECENT__*/null",
    "/*__PRINCIPLES_SET__*/null",
    "/*__CLARIFY_FINDINGS__*/null",
    "/*__CLARIFY_FINALIZE__*/null",
    "/*__IMPLEMENT_READINESS__*/null",
    "/*__CLARIFY_SETTLED__*/null",
    "/*__CLARIFY_PENDING_OVERLAY__*/null",
    "/*__CLARIFY_OVERLAY_WARN_THRESHOLD__*/null",
    "/*__BACKENDS_STATUS__*/null",
    "/*__SKIP_MINOR_FINDINGS__*/null",
]

# Resources the browser would have to FETCH: any src= (script/img/iframe) and the href of
# a <link> (stylesheet/icon). Plain <a href="https://..."> links are fine — the guide pages
# legitimately link out to documentation; they just must never *load* anything remote.
EXTERNAL_SRC_RE = re.compile(r"""\bsrc\s*=\s*["']\s*(?:https?:)?//""", re.IGNORECASE)
EXTERNAL_LINK_RE = re.compile(r"""<link\b[^>]*\bhref\s*=\s*["']\s*(?:https?:)?//""", re.IGNORECASE)


def _external_resource(page: str) -> str | None:
    """The first remotely-fetched resource reference in `page`, or None if it's offline-safe."""
    for pattern in (EXTERNAL_SRC_RE, EXTERNAL_LINK_RE):
        match = pattern.search(page)
        if match:
            return match.group(0)
    return None


def _stylesheet() -> str:
    return (dashboard_assets.ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")


def test_page_template_inlines_the_stylesheet_and_script():
    page = dashboard_assets._page_template()
    assert "/*__CSS__*/" not in page
    assert "/*__JS__*/" not in page
    assert _stylesheet() in page
    assert page.count("<script") >= 1


def test_page_template_still_carries_every_data_placeholder():
    page = dashboard_assets._page_template()
    for placeholder in DATA_PLACEHOLDERS:
        assert placeholder in page, f"{placeholder} is no longer present in the page template"


def test_render_page_substitutes_every_data_placeholder(tmp_path):
    prd_dir = tmp_path / "prd"
    clar_dir = tmp_path / "clarifications"
    prd_dir.mkdir()
    clar_dir.mkdir()
    page = dashboard_assets.render_page(
        prd_dir, clar_dir, {"name": "prd", "type": "dir", "children": []}, [], [], "home")
    for placeholder in DATA_PLACEHOLDERS:
        assert placeholder not in page, f"{placeholder} was left unsubstituted by render_page"


def test_render_page_falls_back_to_the_home_view_for_an_unknown_view(tmp_path):
    prd_dir = tmp_path / "prd"
    clar_dir = tmp_path / "clarifications"
    prd_dir.mkdir()
    clar_dir.mkdir()
    page = dashboard_assets.render_page(
        prd_dir, clar_dir, {"name": "prd", "type": "dir", "children": []}, [], [], "bogus")
    assert 'const INITIAL_VIEW = "home"' in page


def test_the_page_requests_nothing_from_another_host():
    assert _external_resource(dashboard_assets._page_template()) is None


def test_guide_pages_inline_the_same_stylesheet():
    css = _stylesheet()
    for page in (dashboard_assets.principles_guide_page(), dashboard_assets.spec_guide_page()):
        assert "/*__CSS__*/" not in page
        assert css in page
        assert _external_resource(page) is None


def test_javascript_is_served_as_one_inline_script():
    """The JS reaches the browser as inline source in the page, never as a separate
    request — whether it lives in one file on disk or several concatenated ones."""
    page = dashboard_assets._page_template()
    assert "renderMarkdown" in page          # a function defined in the dashboard's JS
    assert "const state = {" in page          # the single shared app-state object
    assert re.search(r"<script\s+src=", page) is None


def test_every_js_part_is_listed_and_every_listed_part_exists():
    """JS_PARTS is the load order, so it has to stay in sync with the folder both ways:
    a part that exists but isn't listed silently never loads."""
    on_disk = {p.name for p in (dashboard_assets.ASSET_DIR / "js").glob("*.js")}
    assert on_disk == set(dashboard_assets.JS_PARTS)
    # The vendored mermaid bundle lives in assets/vendor/ precisely so it stays out of this
    # set: it is served from its own route, never concatenated into the inline script.
    assert "mermaid.min.js" not in dashboard_assets.JS_PARTS
    assert dashboard_assets.JS_PARTS[0] == "00-initial-data.js"    # declares INITIAL_*
    assert dashboard_assets.JS_PARTS[-1] == "99-events-init.js"    # runs the first paint


def test_the_concatenated_script_parses_as_valid_javascript(tmp_path):
    """The parts are concatenated blindly into one script scope, so a cut in the wrong
    place (mid-function, mid-string) would only surface as a blank dashboard in a browser.
    `node --check` parses without executing, which is exactly the check that catches it.
    Skipped when node isn't installed — this is a nice-to-have, not a hard dependency."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping the JS syntax check")
    bundle = tmp_path / "bundle.js"
    bundle.write_text(dashboard_assets._dashboard_js(), encoding="utf-8")
    result = subprocess.run([node, "--check", str(bundle)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# The vendored mermaid bundle (assets/vendor/, served by dashboard_server._serve_mermaid).
#
# Nothing here can execute mermaid or assert an SVG came out of it — that is what the manual
# pass in the PR is for. What these pin down is the part that CAN rot silently: that the
# bundle is present and whole, that it is still the self-contained UMD build (the ESM one
# would work until the first offline diagram, then break), that it never leaks into the page,
# and that the URL the page asks for is the one the server answers.
# ---------------------------------------------------------------------------
def _mermaid_text() -> str:
    return dashboard_assets.mermaid_bundle().decode("utf-8", "replace")


def test_the_vendored_mermaid_bundle_is_present_and_whole():
    bundle = dashboard_assets.mermaid_bundle()
    assert bundle is not None, "src/assets/vendor/mermaid.min.js is missing"
    # A truncated download, an LFS pointer and a CDN error page all land far outside this band.
    assert 2_000_000 < len(bundle) < 8_000_000


def test_the_vendored_bundle_is_the_self_contained_umd_build():
    """The ESM build is a small loader that import()s dozens of sibling chunks — it would
    look fine here and break the first time someone opened a diagram offline."""
    text = _mermaid_text()
    assert "sourceMappingURL" not in text
    assert re.search(r"\bimport\s*\(", text) is None
    for diagram in ("erDiagram", "stateDiagram", "sequenceDiagram"):
        assert diagram in text, f"{diagram} support is missing from the bundle"


def test_the_mermaid_bundle_is_not_inlined_into_the_page():
    """The whole reason it has a route: the page must stay small enough to re-send on every
    load, which is what Cache-Control: no-store makes it do."""
    page = dashboard_assets._page_template()
    assert _mermaid_text()[:400] not in page
    assert len(page) < 1_000_000


def test_every_url_the_page_fetches_at_runtime_is_same_origin():
    """Tightens the offline contract. The page now DOES load a script at runtime, so "no
    <script src=> in the HTML" is no longer the whole story: every URL the JS fetches — via
    fetch() or by assigning to a .src — has to be a path on this same server, never a host.
    (The two https:// links in the Settings help text are hrefs a person clicks, not fetches,
    which is exactly why this looks at the fetch sites rather than at every URL in the file.)"""
    js = dashboard_assets._dashboard_js()
    fetched = re.findall(r"""fetch\(\s*["']([^"']+)["']""", js)
    fetched += re.findall(r"""\.src\s*=\s*["']([^"']+)["']""", js)
    # ...including the ones assigned through a constant, which is how the mermaid URL is held.
    fetched += re.findall(r"""_SRC\s*=\s*["']([^"']+)["']""", js)
    assert fetched, "no fetch sites found — this test has stopped looking at the right thing"
    for url in fetched:
        assert url.startswith("/"), f"{url} is not a same-origin path"


def test_the_page_asks_for_the_mermaid_url_the_server_serves():
    """The route, the version and the cache-buster are declared in two places; this is what
    catches them drifting apart (a stale ?v= serves the old, immutable-cached bundle)."""
    js = dashboard_assets._dashboard_js()
    assert dashboard_assets.MERMAID_ROUTE + "?v=" + dashboard_assets.MERMAID_VERSION in js


def test_the_diagram_styles_and_the_script_agree_on_their_class_names():
    """Nothing else spans that boundary: renaming a class in one file alone would only show
    up as an unstyled diagram in a browser."""
    css, js = _stylesheet(), dashboard_assets._dashboard_js()
    for name in ("mermaid-diagram", "mermaid-source", "mermaid-error"):
        assert name in css and name in js
