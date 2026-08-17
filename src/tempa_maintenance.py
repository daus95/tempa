"""Destructive maintenance: clear and reset commands.

Deletes harness output (qa/, logs/, specs/pbi, specs/clarifications) and resets epic/QA
state in config.json, each behind a confirmation (`_confirm_destructive`) and a safety check
(`_safety_check_clear_target`) that refuses to delete a drive root or anything outside
workspace.root. `run_clear_all` runs the three clears together behind one prompt.

The status resets are non-destructive by comparison (config.json only) — `reset_failed_epics`
is also reused outside the CLI, by implement's automatic retry after a provider overload.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from tempa_config import (
    get_logs_dir,
    get_qa_dir,
    get_sources,
    get_workspace,
    load_config,
    save_config,
)
from tempa_logging import _banner, log
from tempa_qa_history import append_reset_marker


def _confirm_destructive(cancel_message: str) -> None:
    """Ask for interactive "yes" confirmation before a destructive delete (skippable with
    --yes). Exits the process if not confirmed — never returns in that case."""
    if "--yes" in sys.argv:
        return
    if not sys.stdin.isatty():
        log("Aborted — confirmation required. Run in an interactive terminal, or add --yes.")
        sys.exit(1)
    try:
        answer = input('Type "yes" to confirm the deletion (anything else cancels): ').strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        log(cancel_message)
        sys.exit(0)


def _safety_check_clear_target(dir_path: Path, root: str) -> None:
    """Never delete a drive root, or a folder outside workspace.root."""
    if dir_path == dir_path.parent:
        log(f"ERROR: invalid clear target (drive root): {dir_path}")
        sys.exit(1)
    if root and Path(root).resolve() not in dir_path.resolve().parents and Path(root).resolve() != dir_path.resolve():
        log(f"ERROR: clear target ({dir_path}) is outside workspace.root ({root}). Aborted for safety.")
        sys.exit(1)


def _do_clear_implement() -> tuple[int, int]:
    """Delete all contents of the active workspace's qa/ and logs/ folders (under
    .tempa/, not workspace.root-relative). Returns (qa file count, logs file count) deleted."""
    qa_dir = get_qa_dir()
    qa_count = 0
    if qa_dir.exists():
        qa_count = sum(1 for p in qa_dir.rglob("*") if p.is_file())
        for child in qa_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    logs_dir = get_logs_dir()
    logs_count = 0
    if logs_dir.exists():
        logs_count = sum(1 for p in logs_dir.rglob("*") if p.is_file())
        for child in logs_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    return qa_count, logs_count


def _do_clear_plan(config: dict, pbi_dir: Path) -> int:
    """Delete all contents of pbi_dir and empty config["epic"] (caller still has to save
    config). Returns the file count deleted."""
    file_count = sum(1 for p in pbi_dir.rglob("*") if p.is_file()) if pbi_dir.exists() else 0
    if pbi_dir.exists():
        for child in pbi_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    config["epic"] = []
    return file_count


def _do_clear_clarify(clar_dir: Path) -> int:
    """Delete everything in clar_dir except a file named claude.md (case-insensitive).
    Returns the item count deleted."""
    keep = {"claude.md"}
    to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []
    for p in to_delete:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    return len(to_delete)


def _reset_clarify_config_state(config: dict) -> None:
    """Clear every config.json field that tracks a clarification run's outcome
    (caller still has to save_config). Used by `tempa clear` right after it deletes
    the clarification files themselves — without this, "last_clarification_action" /
    "clarify_applied_hashes" would still describe a run that no longer has any files
    to point at, and the dashboard's Finalize gate would misread that leftover state
    as "a clarification action already completed" even though nothing has run yet."""
    config.pop("last_clarification_action", None)
    config.pop("last_clarification_findings", None)
    config.pop("clarify_applied_hashes", None)
    config.pop("last_clean_evaluation_at", None)
    # Resumable session ids (see tempa_config.get_clarify_session_id /
    # get_clarify_apply_session_id) are meaningless once the clarification files they
    # point at are gone — leaving them would risk a later apply/evaluate --resume-ing a
    # session about a backlog that no longer exists.
    config.pop("clarify_session_id", None)
    config.pop("clarify_session_backend", None)
    config.pop("clarify_apply_session_id", None)
    config.pop("clarify_apply_session_backend", None)
    config["last_clarification_round"] = 0
    config["last_finalize_round"] = 0
    config["last_auto_answer"] = 0


def run_clarify_clear() -> None:
    """Clear clarifications: delete everything in the sources.clarifications folder EXCEPT
    a file named claude.md (case-insensitive). Asks for interactive confirmation; skip with
    --yes. Does not touch config.json."""
    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    clar_dir = Path(clarifications_path)
    _safety_check_clear_target(clar_dir, get_workspace(config).get("root", ""))

    keep = {"claude.md"}  # kept (case-insensitive)
    to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []

    if not to_delete:
        log(f"No clarification files to delete in {clar_dir} (other than claude.md).")
        sys.exit(0)

    file_count = sum(1 for p in to_delete if p.is_file())
    dir_count = sum(1 for p in to_delete if p.is_dir())

    _banner("CLARIFICATION CLEAR — DESTRUCTIVE ACTION")
    print(f"  Folder: {clar_dir} | delete: {file_count} file(s) + {dir_count} folder(s) (permanent) | kept: claude.md", flush=True)
    _confirm_destructive("Clarification clear CANCELLED — nothing was changed.")

    deleted = _do_clear_clarify(clar_dir)
    log(f"Clarification clear done — {deleted} item(s) deleted in {clar_dir} (claude.md kept).")
    sys.exit(0)


def run_plan_clear() -> None:
    """Clear the --plan result: empty the "epic" array in config.json AND delete everything
    in the pbi folder (parent of sources.epics). Asks for interactive confirmation before
    deleting. Skip confirmation with --yes (e.g. for non-interactive use)."""
    config = load_config()
    sources = get_sources(config)
    epics_path = sources.get("epics", "")
    if not epics_path:
        log("ERROR: sources.epics not found in config.json")
        sys.exit(1)

    pbi_dir = Path(epics_path).parent
    _safety_check_clear_target(pbi_dir, get_workspace(config).get("root", ""))

    files = [p for p in pbi_dir.rglob("*") if p.is_file()] if pbi_dir.exists() else []
    epic_count = len(config.get("epic") or [])

    _banner("PLAN CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {pbi_dir} ({len(files)} file(s), all sub-folders) | "
          f"Empty \"epic\" array: config.json ({epic_count} entry(ies) → 0)", flush=True)
    _confirm_destructive("Plan clear CANCELLED — nothing was changed.")

    file_count = _do_clear_plan(config, pbi_dir)
    save_config(config)

    log(f"Plan clear done — contents of {pbi_dir} deleted ({file_count} file(s)), \"epic\" array emptied.")
    sys.exit(0)


def run_implement_clear() -> None:
    """Delete all contents of the active workspace's own qa/ and logs/ folders (QA reports &
    session logs, under <workspace_root>/.tempa/ — not workspace.root-relative otherwise).
    Asks for interactive confirmation before deleting. Skip confirmation with --yes."""
    qa_dir = get_qa_dir()
    logs_dir = get_logs_dir()
    qa_files = [p for p in qa_dir.rglob("*") if p.is_file()] if qa_dir.exists() else []
    log_files = [p for p in logs_dir.rglob("*") if p.is_file()] if logs_dir.exists() else []

    if not qa_files and not log_files:
        log(f"Nothing to clear — {qa_dir} and {logs_dir} are already empty.")
        sys.exit(0)

    _banner("IMPLEMENT CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {qa_dir} ({len(qa_files)} file(s)) | {logs_dir} ({len(log_files)} file(s))", flush=True)
    _confirm_destructive("Implement clear CANCELLED — nothing was changed.")

    qa_count, logs_count = _do_clear_implement()
    log(f"Implement clear done — {qa_count} file(s) deleted in {qa_dir}, {logs_count} file(s) deleted in {logs_dir}.")
    sys.exit(0)


def run_clear_all() -> None:
    """Run implement --clear, implement --clear-plan, and clarify --clear together,
    behind a single confirmation prompt. Missing sources.epics/sources.clarifications keys
    still error out (same as the standalone commands); already-empty targets are just skipped."""
    config = load_config()
    sources = get_sources(config)
    root = get_workspace(config).get("root", "")

    epics_path = sources.get("epics", "")
    if not epics_path:
        log("ERROR: sources.epics not found in config.json")
        sys.exit(1)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    pbi_dir = Path(epics_path).parent
    clar_dir = Path(clarifications_path)
    _safety_check_clear_target(pbi_dir, root)
    _safety_check_clear_target(clar_dir, root)

    qa_dir = get_qa_dir()
    logs_dir = get_logs_dir()
    qa_files = [p for p in qa_dir.rglob("*") if p.is_file()] if qa_dir.exists() else []
    log_files = [p for p in logs_dir.rglob("*") if p.is_file()] if logs_dir.exists() else []
    plan_files = [p for p in pbi_dir.rglob("*") if p.is_file()] if pbi_dir.exists() else []
    epic_count = len(config.get("epic") or [])
    keep = {"claude.md"}
    clar_to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []
    # Stale leftovers from a past clarify run can outlive the files they describe
    # (e.g. a previous `clear` already deleted everything on disk but predates
    # _reset_clarify_config_state) — treat that as "still something to clear" too,
    # or this early-exit would leave them behind forever.
    stale_clarify_state = (
        any(k in config for k in ("last_clarification_action", "last_clarification_findings", "clarify_applied_hashes"))
        or config.get("last_auto_answer", 0) != 0
        or config.get("last_clarification_round", 0) != 0
        or config.get("last_finalize_round", 0) != 0
        or config.get("last_clean_evaluation_at", 0) != 0
    )

    if not qa_files and not log_files and not plan_files and epic_count == 0 and not clar_to_delete and not stale_clarify_state:
        log("Nothing to clear — qa/, logs/, specs/pbi, and specs/clarifications are already empty.")
        sys.exit(0)

    _banner("CLEAR ALL — DESTRUCTIVE ACTION")
    print(f"  Implement : {qa_dir} ({len(qa_files)} file(s)) | {logs_dir} ({len(log_files)} file(s))", flush=True)
    print(f"  Plan      : {pbi_dir} ({len(plan_files)} file(s), all sub-folders) | "
          f"empty \"epic\" array: config.json ({epic_count} entry(ies) → 0)", flush=True)
    print(f"  Clarify   : {clar_dir} ({len(clar_to_delete)} item(s), except claude.md)", flush=True)
    _confirm_destructive("Clear CANCELLED — nothing was changed.")

    qa_count, logs_count = _do_clear_implement()
    plan_file_count = _do_clear_plan(config, pbi_dir)
    clarify_count = _do_clear_clarify(clar_dir)
    _reset_clarify_config_state(config)
    save_config(config)

    log(f"Clear done — implement: {qa_count} qa file(s) + {logs_count} log file(s) deleted; "
        f"plan: {plan_file_count} file(s) deleted + epic array emptied; "
        f"clarify: {clarify_count} item(s) deleted.")
    sys.exit(0)


def _epic_features_actually_done(epic: dict) -> bool:
    """True iff `epic`'s own bookkeeping backs up its epic-level "done" status: every
    feature is itself marked "done" and completed_features matches total_features. Returns
    True (nothing to check, trust the epic-level status) when there's no "features" list at
    all.

    The AI agent is solely responsible for this bookkeeping (see the MANDATORY RULE in
    build_session_prompt: mark each feature done + increment completed_features, and only
    THEN set the epic-level status to done) — if it skips a step, nothing catches a "done"
    epic whose features were never actually finished until QA happens to fail again, or
    silently passes against unfinished work. Used by check_and_run's QA gate (to route such
    an epic back to require_fixing before ever running QA on it) and reset_qa_state (the
    same check, applied when manually forcing a re-check on an already-QA'd epic)."""
    features = epic.get("features") or []
    if not features:
        return True
    total = epic.get("total_features", len(features))
    completed = epic.get("completed_features", 0)
    return completed == total and all(f.get("status") == "done" for f in features)


def reconcile_qa_passed_features(config: dict) -> list[tuple[str, int]]:
    """Bring the feature-level bookkeeping of every QA-PASSED epic back in line with the
    QA verdict, and return [(label, features_flipped)] for the epics that were repaired —
    the caller still has to save_config. Empty list = everything already consistent.

    Why this is needed: the QA prompt's PASS branch (src/prompt/qa.md) only tells the agent
    to set qa_passed/qa_status — the FAIL branch is what rewrites feature statuses to
    "require_fixing" and recalculates completed_features. So in the normal
    QA-fails → re-implement → QA-passes cycle, the "require_fixing" feature statuses (and
    the completed_features count QA reset along with them) are only ever cleaned up by the
    re-implementation agent's own per-feature bookkeeping — and when that agent skips the
    step (it marks the epic done without touching each feature, a routine LLM slip), the
    epic ends up permanently "done + QA passed" while its features still read
    "require_fixing" and completed_features reads 0/N. That state is self-contradictory: it
    misreports progress in `tempa status` and the dashboard, and it makes a later
    --reset-qa reroute the epic back into a pointless re-implementation round
    (reset_qa_state uses _epic_features_actually_done).

    Trusting the QA verdict over the feature statuses is the correct direction here: QA only
    ever runs on an epic whose features were ALL already marked done (check_and_run's QA gate
    enforces that via _epic_features_actually_done before dispatching QA), and a pass means
    QA re-verified every feature against the spec. The epic-level status/qa_status guards
    below are what keep this from touching an interrupted QA session's in-flight state: a QA
    agent that finds problems sets the EPIC-level status to "require_fixing" first, before it
    rewrites any feature status, so a half-written failure verdict never satisfies
    status == "done"."""
    repaired: list[tuple[str, int]] = []
    for i, epic in enumerate(config.get("epic") or []):
        if not (epic.get("status") == "done" and epic.get("qa_passed", False)
                and epic.get("qa_status") == "done"):
            continue
        features = epic.get("features") or []
        if not features or _epic_features_actually_done(epic):
            continue
        flipped = sum(1 for f in features if f.get("status") != "done")
        for feature in features:
            feature["status"] = "done"
        epic["completed_features"] = len(features)
        if epic.get("total_features", 0) <= 0:
            epic["total_features"] = len(features)
        repaired.append((epic.get("epic_name", f"epic_{i}"), flipped))
    return repaired


def reconcile_qa_passed_features_and_log(config: dict) -> bool:
    """reconcile_qa_passed_features + a log line per repaired epic. Returns True if anything
    changed, i.e. if the caller needs to save_config."""
    repaired = reconcile_qa_passed_features(config)
    for label, flipped in repaired:
        log(f"[{label}] passed QA but still had {flipped} feature(s) left marked "
            "require_fixing/pending from an earlier failed QA round — marked them done and "
            "resynced completed_features to match the QA verdict.")
    return bool(repaired)


# How a human gets a halted epic moving again, appended to every `blocked_reason` a guard
# writes. That string is the entirety of what the dashboard's Halted panel and `tempa status`
# show for a failed epic; the remediation used to live only in the log line written beside it,
# so the one place a user actually reads about the halt never said what to do about it. The
# dashboard route comes first because it needs no terminal at all: Continue Implementation runs
# `--reset-failed` itself before every implement pass (see dashboard_runs._implement_run_worker),
# so the button a user is already looking at is the whole recovery path.
RETRY_HINT = (
    "To retry: resolve what's described above, then click Continue Implementation on the "
    "dashboard — it resets failed epics for you. From a terminal, `tempa implement "
    "--reset-failed` does the same thing."
)


def with_retry_hint(reason: str) -> str:
    """`reason` with RETRY_HINT appended — the standard shape of a `blocked_reason`.

    Idempotent, so a reason that is decorated and then passed through again (e.g.
    _reason_with_counterpart_context folding one epic's stored reason into another's) doesn't
    accumulate the hint twice."""
    reason = (reason or "").rstrip()
    if RETRY_HINT in reason:
        return reason
    return f"{reason}\n\n{RETRY_HINT}" if reason else RETRY_HINT


def reset_failed_epics(config: dict) -> list[str]:
    """Flip every `failed` epic in `config` back to `pending` for a genuine clean-slate retry
    — dropping its stored session id (so the next attempt starts a fresh session) AND its
    run/stall counters (total_run, qa_total_run, no_progress_rounds, qa_loop_strikes,
    blocked_reason, blocked_by_epic) — and return the labels that were reset — the caller still
    has to save_config. Empty list = nothing was failed.

    Without clearing those counters too, a "reset" epic that hit max_session_run or the
    no-forward-progress guard (see _validate_and_increment_run / _update_no_progress_tracking
    in tempa_session.py) would just immediately re-trip the exact same limit on its very next
    attempt, making --reset-failed look like it worked while actually being a dead end.

    The QA round history is the one thing NOT cleared: it is the only record a human has that
    this epic was cycling through QA, and `tempa status` prints it. It gets a reset marker
    appended instead (append_reset_marker), which is what makes the QA loop guard start counting
    from here — the same clean slate, without destroying the evidence that led to it.

    Shared by two callers: the `implement --reset-failed` command (_reset_failed_epics
    below) and implement's automatic retry after a transient backend overload
    (tempa_implement._reset_failed_before_retry), which has to clear a leftover `failed`
    status itself or check_and_run would refuse to resume anything at all — a clean slate is
    just as correct there, since an overload-caused "failure" isn't a real one either."""
    reset: list[str] = []
    for i, session in enumerate(config.get("epic") or []):
        if session.get("status") == "failed":
            reset.append(session.get("epic_name", f"epic_{i}"))
            session["status"] = "pending"
            session.pop("claude_session_id", None)
            session.pop("session_id", None)
            session.pop("session_backend", None)
            session["total_run"] = 0
            session["qa_total_run"] = 0
            session["no_progress_rounds"] = 0
            session["qa_loop_strikes"] = 0
            append_reset_marker(session)
            session.pop("blocked_reason", None)
            session.pop("blocked_by_epic", None)
    return reset


def _reset_failed_epics() -> None:
    config = load_config()
    reset = reset_failed_epics(config)
    for label in reset:
        log(f"Reset [{label}] → pending")
    if not reset:
        log("No failed sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {len(reset)} failed session(s). Ready to restart.")


def reset_qa_state(config: dict, epic_name: str | None = None) -> list[tuple[str, bool]]:
    """Reset QA state for every "done" epic with QA history — or just `epic_name`, if given
    — so QA is forced to run/re-run, and return [(epic_name, rerouted), ...] for what was
    reset (`rerouted` is True when the epic was also sent back to "require_fixing" — see
    below); the caller still has to save_config. Empty list = nothing matched.

    Also runs the same feature-completeness integrity check as check_and_run's QA gate
    (_epic_features_actually_done): an epic whose features were never actually finished
    (e.g. a re-implementation round set the epic done without finishing each feature's own
    bookkeeping) is sent back to "require_fixing" instead of staying "done" — otherwise
    resetting its QA state alone would just immediately re-trigger QA against the same
    incomplete work, since check_and_run's QA gate acts on "done" epics regardless of how
    they got there.

    Forcing QA to re-run also restarts the QA loop guard's window (qa_loop_strikes +
    append_reset_marker), for the same reason reset_failed_epics does: the rounds before a
    deliberate human re-check shouldn't count against the rounds after it. The history itself is
    kept — see reset_failed_epics."""
    reset: list[tuple[str, bool]] = []
    for i, session in enumerate(config.get("epic") or []):
        label = session.get("epic_name", f"epic_{i}")
        if epic_name is not None and label != epic_name:
            continue
        if session["status"] != "done":
            continue
        if not (session.get("qa_passed", False) or session.get("qa_status") in ("ongoing", "done")):
            continue
        session["qa_passed"] = False
        session["qa_status"] = "idle"
        session["qa_session_id"] = ""
        session.pop("qa_session_backend", None)
        session["qa_total_run"] = 0
        session["qa_loop_strikes"] = 0
        append_reset_marker(session)
        session["qa_report_filename"] = ""
        rerouted = not _epic_features_actually_done(session)
        if rerouted:
            session["status"] = "require_fixing"
        reset.append((label, rerouted))
    return reset


def _reset_qa_state(epic_name: str | None = None) -> None:
    config = load_config()
    reset = reset_qa_state(config, epic_name)
    for label, rerouted in reset:
        suffix = (
            " — its features weren't all actually done, so it's routed back to "
            "require_fixing first" if rerouted else ""
        )
        log(f"Reset QA [{label}] → qa_passed=false, qa_status=idle{suffix}")
    if not reset:
        if epic_name:
            log(f"No done epic named '{epic_name}' with QA state found — nothing to reset")
        else:
            log("No done epics with QA state found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset QA for {len(reset)} epic(s). QA will be re-run.")


def _reset_on_progress_epics() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "on_progress":
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["status"] = "pending"
            config["epic"][i].pop("claude_session_id", None)
            config["epic"][i].pop("session_id", None)
            config["epic"][i].pop("session_backend", None)
            reset_count += 1
            log(f"Reset [{label}] → pending (session_id cleared)")
    if reset_count == 0:
        log("No on_progress sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {reset_count} session(s). Ready to restart.")
