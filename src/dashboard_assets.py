"""Load and compose the dashboard's static front-end assets (assets/dashboard.{html,css,js}).

Keeps the page a single self-contained document: the CSS and JS are read from disk and inlined
into the HTML shell, then the per-request data placeholders are filled in. The page never
contacts another host; the one asset it does fetch at runtime is the vendored mermaid bundle
below, served by this same server (see MERMAID_ROUTE).
Assets are located relative to this file so they resolve whether dashboard_ui is imported,
spawned as a subprocess, or the whole folder is dropped somewhere on PATH."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import tempa_backend
import tempa_config
from dashboard_clarify_parse import (
    _clarification_settled_status,
    _clarify_finalize_status,
    _implement_readiness_status,
    _latest_evaluation_findings,
    _spec_changed_since_evaluation,
    pending_overlay_stats,
)
from dashboard_config import (
    _load_dashboard_config,
    _recent_workspaces,
    _workspace_can_close,
    _workspace_initialized,
    _workspace_root,
)

ASSET_DIR = Path(__file__).resolve().parent / "assets"

# The one third-party front-end asset Tempa vendors (see assets/vendor/README.md). Unlike
# dashboard.css and the assets/js/ parts it is NOT inlined into the page: at ~3.5 MB it would
# be paid on every page load, by every document, for a feature only some markdown files use.
# It gets its own route instead, and the page fetches it lazily the first time a rendered
# document actually contains a ```mermaid block (see assets/js/12-mermaid.js). Still no other
# host involved — this server serves it, so the dashboard keeps working with no network.
# The version travels in the URL's QUERY STRING rather than the filename: that keeps the
# response safely cacheable forever while leaving the file overwritable in place by
# `tempa update` (a versioned filename would strand a 3.5 MB orphan on every upgrade).
MERMAID_VERSION = "11.16.1"
MERMAID_ROUTE = "/assets/mermaid.min.js"


@lru_cache(maxsize=1)
def mermaid_bundle() -> bytes | None:
    """The vendored mermaid UMD bundle (read once, cached), or None if it isn't there.

    None rather than an exception so an install missing the file degrades to showing mermaid
    blocks as the plain code they are rendered as today, instead of 500-ing the route."""
    try:
        return (ASSET_DIR / "vendor" / "mermaid.min.js").read_bytes()
    except OSError:
        return None


# The dashboard's front-end script, split across assets/js/ purely so no single source file
# has to hold all 3,500 lines of it. They are CONCATENATED IN THIS ORDER into one inline
# <script>, so every part shares one script scope exactly as the single file did: no ES
# modules, no imports, no exports. Two ordering rules follow from that and are the only
# reason this is an explicit list rather than a sorted glob:
#   - "00-initial-data.js" must come first — it declares the INITIAL_* constants that
#     render_page() substitutes the per-request data into.
#   - "99-events-init.js" must come last — unlike the others it doesn't just declare
#     functions, it runs the first paint, so everything it calls has to exist by then.
# The numeric prefixes make that order visible in a directory listing; adding a part means
# adding it here too.
JS_PARTS = (
    "00-initial-data.js",
    "10-markdown.js",
    "12-mermaid.js",
    "20-dom-state.js",
    "30-modals.js",
    "40-navigation.js",
    "50-home.js",
    "60-clarify-overview.js",
    "62-backend-status.js",
    "64-clarify-run.js",
    "66-clarify-stop.js",
    "70-implement.js",
    "72-decisions.js",
    "75-verify.js",
    "80-settings-form.js",
    "82-settings-save.js",
    "85-implement-requirement.js",
    "88-principles.js",
    "90-spec.js",
    "92-spec-context-menu.js",
    "94-clarify-answers.js",
    "96-spec-peek.js",
    "99-events-init.js",
)


def _dashboard_js() -> str:
    """The whole front-end script as one string (see JS_PARTS for the ordering rules)."""
    return "".join(
        (ASSET_DIR / "js" / name).read_text(encoding="utf-8") for name in JS_PARTS
    )


@lru_cache(maxsize=1)
def _page_template() -> str:
    """The full HTML document with CSS and JS inlined (read once, cached). The per-request
    data placeholders (SPEC_TREE, etc.) are still present and filled in by render_page()."""
    html = (ASSET_DIR / "dashboard.html").read_text(encoding="utf-8")
    css = (ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css).replace("/*__JS__*/", _dashboard_js())


@lru_cache(maxsize=1)
def principles_guide_page() -> str:
    """The "Learn more" page linked from the Architecture Principles pane. A standalone
    document (opened in its own tab) that inlines the same stylesheet, so it inherits the
    dashboard's markdown typography and light/dark theming."""
    html = (ASSET_DIR / "principles-guide.html").read_text(encoding="utf-8")
    css = (ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css)


@lru_cache(maxsize=1)
def spec_guide_page() -> str:
    """The "Learn more" page linked from the Upload Specification step. A standalone
    document (opened in its own tab) that inlines the same stylesheet, so it inherits the
    dashboard's markdown typography and light/dark theming."""
    html = (ASSET_DIR / "spec-guide.html").read_text(encoding="utf-8")
    css = (ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css)


def render_page(prd_dir: Path, clar_dir: Path, spec_tree: dict, clarify_unanswered: list[dict],
                  clarify_answered: list[dict], initial_view: str) -> str:
    """`clar_dir` is only needed for the pending-overlay stats, which have to be computed
    here rather than derived from `clarify_unanswered`/`clarify_answered`: those carry
    per-file counts, while the overlay is a per-FINDING set (a partially-answered file
    contributes its answered items). Computing it from the same source as /api/tree is what
    keeps the first paint and the first refresh from disagreeing."""
    tree_json = json.dumps(spec_tree, ensure_ascii=False)
    unanswered_json = json.dumps(clarify_unanswered, ensure_ascii=False)
    answered_json = json.dumps(clarify_answered, ensure_ascii=False)
    prd_name = json.dumps(prd_dir.name, ensure_ascii=False)
    view_json = json.dumps(initial_view if initial_view in ("home", "specification", "clarification") else "home")
    workspace_initialized_json = json.dumps(_workspace_initialized())
    workspace_root_json = json.dumps(_workspace_root())
    workspace_can_close_json = json.dumps(_workspace_can_close())
    workspace_recent_json = json.dumps(_recent_workspaces(), ensure_ascii=False)
    principles_set_json = json.dumps(bool(tempa_config.read_principles()))
    dashboard_config = _load_dashboard_config()
    latest_findings = _latest_evaluation_findings(
        clarify_unanswered + clarify_answered, dashboard_config.get("last_clean_evaluation_at", 0)
    )
    clarify_findings_json = json.dumps(latest_findings, ensure_ascii=False)
    last_action = dashboard_config.get("last_clarification_action")
    round_ = dashboard_config.get("last_clarification_round") or 0
    max_round = dashboard_config.get("max_clarification_run") or 0
    finalize_round = dashboard_config.get("last_finalize_round") or 0
    allow_finalize_with_critical = bool(dashboard_config.get("allow_finalize_with_critical"))
    overlay = pending_overlay_stats(clar_dir, dashboard_config.get("clarify_applied_hashes", {}) or {})
    pending_overlay_json = json.dumps(overlay, ensure_ascii=False)
    overlay_warn_threshold_json = json.dumps(
        tempa_config.get_clarify_overlay_warn_findings(dashboard_config)
    )
    clarify_finalize_json = json.dumps(
        _clarify_finalize_status(
            latest_findings, last_action, round_, max_round, allow_finalize_with_critical,
            finalize_round, overlay["findings"]),
        ensure_ascii=False,
    )
    implementation_requirement = tempa_config.get_implementation_start_requirement(dashboard_config)
    # Same raw/masked split as tree_payload — keep these two computations identical or the
    # first paint and the first refresh will disagree about a disabled button.
    major_sweep_pending = tempa_config.severity_sweep_pending(dashboard_config)
    skip_minor_findings = tempa_config.get_skip_minor_findings(dashboard_config)
    spec_changed = _spec_changed_since_evaluation(
        clarify_unanswered + clarify_answered,
        dashboard_config.get("last_clean_evaluation_at", 0),
        dashboard_config.get("spec_changed_at", 0),
    )
    implement_readiness_json = json.dumps(
        _implement_readiness_status(
            latest_findings, last_action is not None, implementation_requirement,
            overlay["findings"], major_sweep_pending, spec_changed),
        ensure_ascii=False,
    )
    clarify_settled_json = json.dumps(
        _clarification_settled_status(
            latest_findings, last_action, len(clarify_unanswered), major_sweep_pending,
            skip_minor_findings, spec_changed),
        ensure_ascii=False,
    )
    backends_status_json = json.dumps(
        tempa_backend.get_backend_status(tempa_config.workspace_is_writable(_workspace_root())),
        ensure_ascii=False,
    )
    skip_minor_findings_json = json.dumps(skip_minor_findings)
    # The picker's options travel with the page rather than being hard-coded in the JS, so
    # CLARIFICATION_LANGUAGES stays the single place a language is added or renamed.
    clarify_language_json = json.dumps(
        tempa_config.get_clarification_language(dashboard_config))
    clarify_languages_json = json.dumps(
        [{"code": code, "label": label} for code, _name, label in tempa_config.CLARIFICATION_LANGUAGES],
        ensure_ascii=False,
    )
    return (
        _page_template()
        .replace("/*__SPEC_TREE__*/null", tree_json)
        .replace("/*__CLARIFY_UNANSWERED__*/null", unanswered_json)
        .replace("/*__CLARIFY_ANSWERED__*/null", answered_json)
        .replace("/*__PRD_NAME__*/null", prd_name)
        .replace("/*__INITIAL_VIEW__*/null", view_json)
        .replace("/*__WORKSPACE_INITIALIZED__*/null", workspace_initialized_json)
        .replace("/*__WORKSPACE_ROOT__*/null", workspace_root_json)
        .replace("/*__WORKSPACE_CAN_CLOSE__*/null", workspace_can_close_json)
        .replace("/*__WORKSPACE_RECENT__*/null", workspace_recent_json)
        .replace("/*__PRINCIPLES_SET__*/null", principles_set_json)
        .replace("/*__CLARIFY_FINDINGS__*/null", clarify_findings_json)
        .replace("/*__CLARIFY_FINALIZE__*/null", clarify_finalize_json)
        .replace("/*__IMPLEMENT_READINESS__*/null", implement_readiness_json)
        .replace("/*__CLARIFY_SETTLED__*/null", clarify_settled_json)
        .replace("/*__CLARIFY_PENDING_OVERLAY__*/null", pending_overlay_json)
        .replace("/*__CLARIFY_OVERLAY_WARN_THRESHOLD__*/null", overlay_warn_threshold_json)
        .replace("/*__BACKENDS_STATUS__*/null", backends_status_json)
        .replace("/*__SKIP_MINOR_FINDINGS__*/null", skip_minor_findings_json)
        .replace("/*__CLARIFY_LANGUAGE__*/null", clarify_language_json)
        .replace("/*__CLARIFY_LANGUAGES__*/null", clarify_languages_json)
    )


# The page is a single self-contained document: no CSS/JS/fonts from another host, and a
# small hand-written markdown renderer in JS so it works fully offline. The sole runtime
# fetch is the vendored mermaid bundle above, from this same server, and only for documents
# that actually contain a diagram.
