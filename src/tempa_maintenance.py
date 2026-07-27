"""Destructive maintenance: clear and reset commands.

Deletes harness output (qa/, logs/, specs/pbi, specs/clarifications) and resets epic/QA
state in config.json, each behind a confirmation (`_confirm_destructive`) and a safety check
(`_safety_check_clear_target`) that refuses to delete a drive root or anything outside
workspace.root. `run_clear_all` runs the three clears together behind one prompt.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from tempa_config import (
    LOGS_DIR, QA_DIR, get_sources, get_workspace, load_config, save_config,
)
from tempa_logging import _banner, log


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
    """Delete all contents of QA_DIR and LOGS_DIR (the harness's own qa/log output — not
    workspace-relative). Returns (qa file count, logs file count) deleted."""
    qa_count = 0
    if QA_DIR.exists():
        qa_count = sum(1 for p in QA_DIR.rglob("*") if p.is_file())
        for child in QA_DIR.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    logs_count = 0
    if LOGS_DIR.exists():
        logs_count = sum(1 for p in LOGS_DIR.rglob("*") if p.is_file())
        for child in LOGS_DIR.iterdir():
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
    epic_count = len((config.get("epic") or []))

    _banner("PLAN CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {pbi_dir} ({len(files)} file(s), all sub-folders) | "
          f"Empty \"epic\" array: config.json ({epic_count} entry(ies) → 0)", flush=True)
    _confirm_destructive("Plan clear CANCELLED — nothing was changed.")

    file_count = _do_clear_plan(config, pbi_dir)
    save_config(config)

    log(f"Plan clear done — contents of {pbi_dir} deleted ({file_count} file(s)), \"epic\" array emptied.")
    sys.exit(0)


def run_implement_clear() -> None:
    """Delete all contents of the harness's own qa/ and logs/ folders (QA reports & session
    logs — not workspace-relative, always SCRIPT_DIR/qa and SCRIPT_DIR/logs). Asks for
    interactive confirmation before deleting. Skip confirmation with --yes."""
    qa_files = [p for p in QA_DIR.rglob("*") if p.is_file()] if QA_DIR.exists() else []
    log_files = [p for p in LOGS_DIR.rglob("*") if p.is_file()] if LOGS_DIR.exists() else []

    if not qa_files and not log_files:
        log(f"Nothing to clear — {QA_DIR} and {LOGS_DIR} are already empty.")
        sys.exit(0)

    _banner("IMPLEMENT CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {QA_DIR} ({len(qa_files)} file(s)) | {LOGS_DIR} ({len(log_files)} file(s))", flush=True)
    _confirm_destructive("Implement clear CANCELLED — nothing was changed.")

    qa_count, logs_count = _do_clear_implement()
    log(f"Implement clear done — {qa_count} file(s) deleted in {QA_DIR}, {logs_count} file(s) deleted in {LOGS_DIR}.")
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

    qa_files = [p for p in QA_DIR.rglob("*") if p.is_file()] if QA_DIR.exists() else []
    log_files = [p for p in LOGS_DIR.rglob("*") if p.is_file()] if LOGS_DIR.exists() else []
    plan_files = [p for p in pbi_dir.rglob("*") if p.is_file()] if pbi_dir.exists() else []
    epic_count = len((config.get("epic") or []))
    keep = {"claude.md"}
    clar_to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []
    # Stale leftovers from a past clarify run can outlive the files they describe
    # (e.g. a previous `clear` already deleted everything on disk but predates
    # _reset_clarify_config_state) — treat that as "still something to clear" too,
    # or this early-exit would leave them behind forever.
    stale_clarify_state = (
        any(k in config for k in ("last_clarification_action", "last_clarification_findings", "clarify_applied_hashes"))
        or config.get("last_auto_answer", 0) != 0
    )

    if not qa_files and not log_files and not plan_files and epic_count == 0 and not clar_to_delete and not stale_clarify_state:
        log("Nothing to clear — qa/, logs/, specs/pbi, and specs/clarifications are already empty.")
        sys.exit(0)

    _banner("CLEAR ALL — DESTRUCTIVE ACTION")
    print(f"  Implement : {QA_DIR} ({len(qa_files)} file(s)) | {LOGS_DIR} ({len(log_files)} file(s))", flush=True)
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


def _reset_failed_epics() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "failed":
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["status"] = "pending"
            config["epic"][i].pop("claude_session_id", None)
            reset_count += 1
            log(f"Reset [{label}] → pending")
    if reset_count == 0:
        log("No failed sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {reset_count} failed session(s). Ready to restart.")


def _reset_qa_state() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "done" and (session.get("qa_passed", False) or session.get("qa_status") in ("ongoing", "done")):
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["qa_passed"] = False
            config["epic"][i]["qa_status"] = "idle"
            config["epic"][i]["qa_session_id"] = ""
            config["epic"][i]["qa_total_run"] = 0
            config["epic"][i]["qa_report_filename"] = ""
            reset_count += 1
            log(f"Reset QA [{label}] → qa_passed=false, qa_status=idle")
    if reset_count == 0:
        log("No done epics with QA state found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset QA for {reset_count} epic(s). QA will be re-run.")


def _reset_on_progress_epics() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "on_progress":
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["status"] = "pending"
            config["epic"][i].pop("claude_session_id", None)
            reset_count += 1
            log(f"Reset [{label}] → pending (session_id cleared)")
    if reset_count == 0:
        log("No on_progress sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {reset_count} session(s). Ready to restart.")
