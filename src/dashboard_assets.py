"""Load and compose the dashboard's static front-end assets (assets/dashboard.{html,css,js}).

Keeps the page a single self-contained document (no external fetches): the CSS and JS are read
from disk and inlined into the HTML shell, then the per-request data placeholders are filled in.
Assets are located relative to this file so they resolve whether dashboard_ui is imported,
spawned as a subprocess, or the whole folder is dropped somewhere on PATH."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import tempa_backend
import tempa_config
from dashboard_clarify_parse import (
    _clarify_finalize_status,
    _implement_readiness_status,
    _latest_evaluation_findings,
)
from dashboard_config import (
    _load_dashboard_config,
    _workspace_can_close,
    _workspace_initialized,
    _workspace_root,
)

ASSET_DIR = Path(__file__).resolve().parent / "assets"


@lru_cache(maxsize=1)
def _page_template() -> str:
    """The full HTML document with CSS and JS inlined (read once, cached). The per-request
    data placeholders (SPEC_TREE, etc.) are still present and filled in by render_page()."""
    html = (ASSET_DIR / "dashboard.html").read_text(encoding="utf-8")
    css = (ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")
    js = (ASSET_DIR / "dashboard.js").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css).replace("/*__JS__*/", js)


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


def render_page(prd_dir: Path, spec_tree: dict, clarify_unanswered: list[dict],
                  clarify_answered: list[dict], initial_view: str) -> str:
    tree_json = json.dumps(spec_tree, ensure_ascii=False)
    unanswered_json = json.dumps(clarify_unanswered, ensure_ascii=False)
    answered_json = json.dumps(clarify_answered, ensure_ascii=False)
    prd_name = json.dumps(prd_dir.name, ensure_ascii=False)
    view_json = json.dumps(initial_view if initial_view in ("home", "specification", "clarification") else "home")
    workspace_initialized_json = json.dumps(_workspace_initialized())
    workspace_root_json = json.dumps(_workspace_root())
    workspace_can_close_json = json.dumps(_workspace_can_close())
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
    clarify_finalize_json = json.dumps(
        _clarify_finalize_status(
            latest_findings, last_action, round_, max_round, allow_finalize_with_critical, finalize_round),
        ensure_ascii=False,
    )
    implementation_requirement = tempa_config.get_implementation_start_requirement(dashboard_config)
    implement_readiness_json = json.dumps(
        _implement_readiness_status(latest_findings, last_action is not None, implementation_requirement),
        ensure_ascii=False,
    )
    backends_status_json = json.dumps(
        tempa_backend.get_backend_status(tempa_config.workspace_is_writable(_workspace_root())),
        ensure_ascii=False,
    )
    skip_minor_findings_json = json.dumps(tempa_config.get_skip_minor_findings(dashboard_config))
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
        .replace("/*__PRINCIPLES_SET__*/null", principles_set_json)
        .replace("/*__CLARIFY_FINDINGS__*/null", clarify_findings_json)
        .replace("/*__CLARIFY_FINALIZE__*/null", clarify_finalize_json)
        .replace("/*__IMPLEMENT_READINESS__*/null", implement_readiness_json)
        .replace("/*__BACKENDS_STATUS__*/null", backends_status_json)
        .replace("/*__SKIP_MINOR_FINDINGS__*/null", skip_minor_findings_json)
    )


# The page is a single self-contained document: no external CSS/JS/fonts, and a
# small hand-written markdown renderer in JS (below) so it works fully offline.
