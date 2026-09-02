"""What the dashboard polls: the first-paint tree, the two live run-status payloads, the
log/QA-report viewers, and the update check.

These are the read-only endpoints. Everything here is recomputed from disk on every call
rather than cached — the Status tab shows the very state a background run is writing into
config.json, so a cached answer would show a run that has already moved on.
"""

from __future__ import annotations

import re
from pathlib import Path

import tempa_backend
import tempa_config
import tempa_update
from dashboard_clarify_parse import (
    _clarification_settled_status,
    _clarify_files_overview,
    _clarify_finalize_status,
    _implement_readiness_status,
    _latest_evaluation_findings,
    _spec_changed_since_evaluation,
    pending_overlay_stats,
)
from dashboard_config import (
    _load_clarify_applied_hashes,
    _load_clarify_file_timings,
    _load_dashboard_config,
    _recent_workspaces,
    _workspace_can_close,
    _workspace_initialized,
    _workspace_root,
)
from dashboard_runs import (
    _clarify_graceful_stop_pending,
    _epic_sessions,
    _implement_graceful_stop_pending,
    _implementation_has_started,
)
from dashboard_spec import _resolve_within, build_tree

Response = tuple[int, dict]

# A session/QA log can legitimately be large (some real ones run past 400KB), but there's
# no reason to ever ship more than this much text into the browser for one modal view — cap
# it and keep the tail (the most recently written, most diagnostically relevant part),
# matching the same tail-first philosophy as tempa_logging._print_log_tail.
LOG_FILE_MAX_CHARS = 5_000_000


def backend_status() -> dict:
    """Per-CLI-backend readiness (installed + workspace-writable) for whichever workspace
    is currently active. Callers share one result per request so the writability probe
    (a real filesystem touch) only runs once, not once per handler that wants it."""
    root = _workspace_root()
    writable = tempa_config.workspace_is_writable(root) if root else False
    return tempa_backend.get_backend_status(writable)


def tree_payload(prd_dir: Path, clar_dir: Path, backends: dict) -> Response:
    """Everything the page needs to (re)paint itself: the workspace, the spec tree, and
    the whole clarification state including both readiness gates."""
    unanswered, answered = _clarify_files_overview(
        clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
    )
    dashboard_config = _load_dashboard_config()
    findings = _latest_evaluation_findings(
        unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0)
    )
    last_action = dashboard_config.get("last_clarification_action")
    round_ = dashboard_config.get("last_clarification_round") or 0
    max_round = dashboard_config.get("max_clarification_run") or 0
    finalize_round = dashboard_config.get("last_finalize_round") or 0
    allow_finalize_with_critical = bool(dashboard_config.get("allow_finalize_with_critical"))
    implementation_requirement = tempa_config.get_implementation_start_requirement(dashboard_config)
    overlay = pending_overlay_stats(clar_dir, _load_clarify_applied_hashes())
    # Raw, unmasked (see _clarification_settled_status) — _implement_readiness_status is
    # what applies the requirement mask to its own copy of these two.
    major_sweep_pending = tempa_config.severity_sweep_pending(dashboard_config)
    skip_minor_findings = tempa_config.get_skip_minor_findings(dashboard_config)
    spec_changed = _spec_changed_since_evaluation(
        unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0),
        dashboard_config.get("spec_changed_at", 0),
    )
    return 200, {
        "ok": True,
        "workspace": {"initialized": _workspace_initialized(), "root": _workspace_root(),
                       "canClose": _workspace_can_close(), "recent": _recent_workspaces()},
        "spec": {"tree": build_tree(prd_dir)},
        "clarify": {"unanswered": unanswered, "answered": answered,
                    "findings": findings,
                    "finalize": _clarify_finalize_status(
                        findings, last_action, round_, max_round, allow_finalize_with_critical,
                        finalize_round, overlay["findings"]),
                    "implementReadiness": _implement_readiness_status(
                        findings, last_action is not None, implementation_requirement,
                        overlay["findings"], major_sweep_pending, spec_changed),
                    "settled": _clarification_settled_status(
                        findings, last_action, len(unanswered), major_sweep_pending,
                        skip_minor_findings, spec_changed),
                    "pendingOverlay": overlay,
                    "overlayWarnThreshold": tempa_config.get_clarify_overlay_warn_findings(
                        dashboard_config),
                    "skipMinorFindings": skip_minor_findings,
                    "severityPhase": (dashboard_config.get("last_severity_phase") or ""
                                      if tempa_config.get_clarify_severity_phases(dashboard_config)
                                      else "")},
        "principles": {"set": bool(tempa_config.read_principles())},
        "backends": backends,
    }


def _since(query: dict) -> int:
    """The client's cursor into the log lines it has already received. A malformed value
    means "start over" rather than an error — the log panel is a view, not a transaction."""
    try:
        return int(query.get("since", ["0"])[0])
    except ValueError:
        return 0


def clarify_run_status(server, query: dict) -> Response:
    since = _since(query)
    run = server.clarify_run
    # Read fresh from config.json on every poll (not cached) so the finalize
    # progress badge next to the "Finalized Clarification" button ticks up live,
    # round by round, the same way implement's epic snapshot does.
    dashboard_config = _load_dashboard_config()
    # Reads the sentinel as well as this process's own flag, so a graceful stop asked
    # for with `tempa clarify --stop-graceful` in a terminal shows up here too.
    graceful_pending = _clarify_graceful_stop_pending(server)
    with run["lock"]:
        lines = list(run["lines"][max(since, 0):])
        total = len(run["lines"])
        return 200, {
            "ok": True, "running": run["running"], "mode": run["mode"],
            "returncode": run["returncode"], "lines": lines, "next": total,
            "progress": run["progress"],
            "gracefulStopRequested": graceful_pending,
            "finalizeRound": dashboard_config.get("last_finalize_round") or 0,
            "maxRound": dashboard_config.get("max_clarification_run") or 0,
            # "evaluate" | "verify" — which kind of round finalize is on (see
            # run_clarify_finalize). A finalize run ends with a verification round over
            # the freshly-compacted PRD, which looks identical in the badge otherwise.
            "finalizePhase": dashboard_config.get("last_finalize_phase") or "",
        }


def implement_run_status(server, query: dict) -> Response:
    since = _since(query)
    run = server.implement_run
    # Epics are read fresh from config.json on every poll (not cached) — the Status
    # tab shows the same data the "Log" tab's run is actively writing into
    # config.json, so it needs to reflect live progress too.
    epics = _epic_sessions()
    # Same as the clarify status above: OR'd with the sentinel so a request made from
    # a terminal (`tempa implement --stop-graceful`) is reflected in the UI too.
    graceful_pending = _implement_graceful_stop_pending(server)
    with run["lock"]:
        lines = list(run["lines"][max(since, 0):])
        total = len(run["lines"])
        return 200, {
            "ok": True, "running": run["running"],
            "returncode": run["returncode"], "lines": lines, "next": total,
            "progress": run["progress"],
            "gracefulStopRequested": graceful_pending,
            "epics": epics,
            # Relabels the three Start Implementation buttons to "Continue
            # Implementation" once any epic has actually run — computed here so
            # every surface agrees (see _implementation_has_started).
            "started": _implementation_has_started(epics),
        }


def _read_capped_file(root: Path, name: str, suffix: str, not_found: str) -> Response:
    """One flat file out of `root`, by bare filename, capped to its last
    LOG_FILE_MAX_CHARS. `_resolve_within` confines this to `root` the same way spec and
    clarify reads are confined to theirs, so a crafted name can't read anything else."""
    target = _resolve_within(root, name)
    if target is None or target.suffix.lower() != suffix or not target.is_file():
        return 404, {"ok": False, "error": not_found}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not read file: {e}"}
    truncated = len(content) > LOG_FILE_MAX_CHARS
    if truncated:
        content = content[-LOG_FILE_MAX_CHARS:]
    return 200, {"ok": True, "name": target.name, "content": content, "truncated": truncated}


def read_log_file(name: str) -> Response:
    """One session/QA/process log by bare filename (no subdirectories — these are all flat
    files directly under .tempa/logs/), for the Log tab's "log: <filename>" links to open
    in a viewer modal."""
    return _read_capped_file(tempa_config.get_logs_dir(), name, ".txt", "Log file not found.")


def read_qa_report(name: str) -> Response:
    """One QA report's raw markdown by bare filename (flat files under .tempa/qa/), for the
    Status tab's per-round QA history (epic.qa_history[].report — see
    tempa_qa_history.record_qa_round) to open in the same modal, rendered as markdown
    instead of the modal's usual `<pre>` text."""
    return _read_capped_file(tempa_config.get_qa_dir(), name, ".md", "QA report not found.")


def update_status() -> Response:
    """Installed version vs. the latest published release. Being unable to reach GitHub is
    reported inside a 200 — it's a best-effort check, not a failed request."""
    current = tempa_update.get_local_version()
    latest = tempa_update.get_latest_release_version()
    if latest is None:
        return 200, {
            "ok": True, "current": current, "latest": None, "updateAvailable": False,
            "error": "Could not reach GitHub to check the latest release.",
        }
    return 200, {
        "ok": True, "current": current, "latest": latest,
        "updateAvailable": current == "unknown" or current != latest,
    }


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def update_changelog(latest: str) -> Response:
    """Changelog entries for every version between the installed one and `latest`
    (inclusive), for the Maintenance tab's "What's New" link. `latest` comes from the
    client (echoing what /api/update/status already told it), so it's validated before
    being used to build the GitHub raw-content URL, the same way _resolve_within validates
    a client-supplied path before it touches disk."""
    if not _VERSION_RE.match(latest):
        return 400, {"ok": False, "error": "Invalid version."}
    current = tempa_update.get_local_version()
    content = tempa_update.get_changelog_since(current, latest)
    if content is None:
        return 200, {"ok": False, "error": "Could not reach GitHub to fetch changelog details."}
    return 200, {"ok": True, "content": content, "truncated": False}
