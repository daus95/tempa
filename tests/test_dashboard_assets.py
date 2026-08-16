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
    "/*__PRINCIPLES_SET__*/null",
    "/*__CLARIFY_FINDINGS__*/null",
    "/*__CLARIFY_FINALIZE__*/null",
    "/*__IMPLEMENT_READINESS__*/null",
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
