"""Clarify / implement background runs (subprocess + live log polling).

Spawns `tempa.py clarify` / `tempa.py implement` as child processes and streams their console
output into per-run state dicts the dashboard polls, plus the Stop-implementation kill and the
live epic snapshot read from config.json."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

from dashboard_clarify_parse import _clarify_files_overview
from dashboard_config import _load_clarify_applied_hashes, _load_dashboard_config
from tempa_config import clear_graceful_stop, graceful_stop_requested, request_graceful_stop
from tempa_session import _read_process_stdout


def _epic_sessions() -> list:
    """config.json's "epic" array — the same per-epic/feature progress data
    `tempa status` (print_status()) formats to the console."""
    epics = _load_dashboard_config().get("epic")
    return epics if isinstance(epics, list) else []


def _implementation_has_started(epics: list | None = None) -> bool:
    """Whether implementation has already been run at least once in this workspace —
    i.e. at least one planned epic has moved off `pending` (on_progress/done/
    require_fixing/failed) or carries a `last_run` stamp.

    Drives the dashboard's Start/Continue Implementation relabeling (the same
    Start/Continue treatment the clarification buttons already get) and is reported
    to the client as the `started` field of /api/implement/run, so all three
    Start Implementation buttons agree on one server-computed answer. A freshly
    planned-but-never-run epic array is NOT "started" — a plan alone doesn't mean
    any work happened."""
    for epic in (_epic_sessions() if epics is None else epics):
        if not isinstance(epic, dict):
            continue
        if epic.get("last_run"):
            return True
        if (epic.get("status") or "pending") != "pending":
            return True
    return False


def _unapplied_answered_count(server) -> int:
    """How many fully-answered clarification files still don't match config.json's
    "clarify_applied_hashes" — i.e. still need an Apply pass. Used by the apply
    auto-chain below to keep applying rather than moving on to evaluate while any
    ready file is still waiting."""
    _, answered = _clarify_files_overview(server.clar_dir, _load_clarify_applied_hashes())
    return sum(1 for f in answered if not f["applied"])


# ---------------------------------------------------------------------------
# Clarification run (Start Clarification / Finalized Clarification buttons) —
# spawns `tempa clarify` / `tempa clarify --finalize` as a subprocess and lets the
# dashboard poll its console output for the collapsible log panel.
# ---------------------------------------------------------------------------
# Matches the self-overwriting `\r[HH:MM:SS] [...] [rows]` progress line tempa.py
# prints once a second while an agent session is running (see _display_progress in
# tempa.py). Kept out of the appended `lines` history entirely (see `progress` below)
# — tempa.py can go minutes between any other console output, so if this were folded
# into `lines` in place, the dashboard's index-based polling would fetch it once and
# then never notice it kept changing, making a live run look frozen.
_PROGRESS_LINE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\].*\[\d+ rows\](\s*\[[^\]]*\])*\s*$")


def _new_clarify_run_state() -> dict:
    return {
        "lock": threading.Lock(),
        "running": False,
        "mode": None,
        "lines": [],
        "progress": None,
        "returncode": None,
        "process": None,
        # Set by _stop_clarify_run for any mode. Its live Popen (if any) is killed
        # immediately regardless of mode; "apply" additionally reads this back between
        # its auto-chained batches (see worker() below) to skip the next batch instead
        # of starting it.
        "stop_requested": False,
        # Set by _graceful_stop_clarify_run — the same "don't start the next thing"
        # intent, but WITHOUT killing what's already running, so the session in progress
        # gets to finish and record its work (see _graceful_stop_clarify_run).
        "graceful_stop_requested": False,
    }


_CLARIFY_RUN_ARGS = {"run": ["--noui"], "finalize": ["--finalize"], "apply": ["--apply"]}


# The run limits `clarify --finalize` snapshots at process start, mapped to the label the
# dashboard Settings pane shows for each (see _finalize_limit_change_warning below).
_FINALIZE_SNAPSHOT_LIMITS = {
    "max_clarification_run": "Max Finalize Clarification Round",
    "finalize_no_progress_rounds": "Max Finalize No-Progress Round",
}


def _finalize_limit_change_warning(server, previous: dict, current: dict) -> str | None:
    """Warning text for saving a changed finalize run limit ("Max Finalize Clarification
    Round" / "Max Finalize No-Progress Round") while a Finalized Clarification run is
    already in progress — or None when there's nothing to warn about.

    `clarify --finalize` reads both limits ONCE, when its process starts (see
    run_clarify_finalize in tempa_clarify.py), and keeps using that snapshot for its whole
    evaluate/apply loop even though it re-reads the rest of config.json every round. So
    lowering a limit mid-run doesn't shorten the run the user is watching: its rounds keep
    counting toward the limits that were in effect when it started ("ROUND 17/25" while the
    Settings field reads 10), which is easily mistaken for the limit not being enforced at
    all. It is enforced — just from the next finalize run onward. This is the one moment
    that misunderstanding forms, so it's the moment to say so.

    Only mode "finalize" is warned about: it's the only run that reads these settings. Fresh
    per-run subprocesses mean nothing needs restarting for the new values to take effect."""
    changed = [(label, previous.get(key), current.get(key))
               for key, label in _FINALIZE_SNAPSHOT_LIMITS.items()
               if previous.get(key) != current.get(key)]
    if not changed:
        return None
    run = server.clarify_run
    with run["lock"]:
        if not (run["running"] and run["mode"] == "finalize"):
            return None
    saved = "; ".join(
        f"{label} was saved as {new}, replacing "
        + (f"its previous limit ({old})" if isinstance(old, int) else "its original limit")
        for label, old, new in changed
    )
    settings_label = "these settings" if len(changed) > 1 else "this setting"
    return (
        f"{saved} — but a Finalized Clarification run is already in progress and will keep "
        f"using the old limits until it stops. It reads {settings_label} once, when it starts, "
        "so the round counter in its log keeps counting toward the old limit. Your new value "
        "applies from the next Finalized Clarification run onward; nothing needs to be "
        "restarted for it to take effect."
    )


def _start_clarify_run(server, mode: str) -> bool:
    """Start `tempa clarify` (mode "run"), `tempa clarify --finalize` (mode "finalize"),
    or `tempa clarify --apply` (mode "apply") as a background subprocess, appending its
    console output to server.clarify_run["lines"] as it streams in. Returns False without
    starting anything if a run is already in progress (defense in depth alongside the
    dashboard disabling the buttons client-side).

    If more than one fully-answered clarification file is still waiting to be applied
    when "apply" is requested, this keeps re-running `clarify --apply` — one file's worth
    of backlog at a time — until every ready file is applied, so one click finishes the
    job the user actually asked for.

    It does NOT chain into a fresh evaluate afterwards. That used to happen because
    Continue Clarification was blocked until everything was applied, which left anyone who
    only ever clicked Apply staring at stale critical/major counts with no way forward.
    Continuing no longer requires an apply at all (answers ride into the next round as the
    pending overlay — see pending_resolutions in dashboard_clarify_parse.py), so spending a
    full evaluate session after every apply is exactly the per-round cost this design
    removes. The user runs Continue Clarification when they want fresh numbers; until they
    do, last_clarification_action stays "apply" and the button says so."""
    run = server.clarify_run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["mode"] = mode
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None
        run["process"] = None
        run["stop_requested"] = False
        run["graceful_stop_requested"] = False
    # A sentinel left behind by an earlier run (killed before it could read it, machine
    # restart) would otherwise stop this one on its first check.
    clear_graceful_stop("clarify")

    def worker() -> None:
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"

        def run_once(args: list[str]) -> int:
            cmd = [sys.executable, str(tempa_py), "clarify", *args]
            try:
                process = subprocess.Popen(
                    cmd,
                    # `tempa clarify --apply` asks (via input()) whether to run another
                    # clarification round right away, but only if stdin is a tty — DEVNULL
                    # guarantees it never is, so a dashboard-triggered apply can't block
                    # forever waiting for a keypress no one can give it. (The dashboard
                    # leaves that call to the user: Continue Clarification is one click
                    # away and no longer requires an apply first.)
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
            except OSError as e:
                with run["lock"]:
                    run["lines"].append(f"[error] Could not start clarify process: {e}")
                return -1
            # Tracked so Stop (see _stop_clarify_run) can kill it. "run" and "finalize"
            # are each a single subprocess for their whole session (finalize's rounds
            # happen inside tempa_clarify.py, not as separate Popen calls here), so one
            # PID is all Stop ever needs to kill. "apply" is spread across multiple
            # Popen calls below (one per backlog batch) — Stop kills whichever one is
            # live, and the stop_requested check below skips any batch still queued.
            with run["lock"]:
                run["process"] = process
            for raw_line in _read_process_stdout(process):
                line = raw_line.strip()
                if not line:
                    continue
                with run["lock"]:
                    if _PROGRESS_LINE_RE.match(line):
                        run["progress"] = line
                    else:
                        run["lines"].append(line)
            process.wait()
            with run["lock"]:
                run["process"] = None
            return process.returncode

        returncode = run_once(_CLARIFY_RUN_ARGS[mode])
        if mode == "apply" and returncode == 0:
            remaining = _unapplied_answered_count(server)
            # Distinguishes "the loop exited because everything's applied" (the only
            # case that should print the "Apply finished" message below) from a stop
            # mid-loop or a stalled (no-progress) exit — otherwise that message would
            # follow right after "Apply Answers stopped..."/"...stopping the auto-apply
            # loop", contradicting it. returncode alone can't tell them apart: it's
            # still 0 in all three cases, since the last individual batch succeeded.
            stopped = False
            stalled = False
            while returncode == 0 and remaining > 0:
                # The sentinel is read here too, not just the in-memory flag, so a
                # `tempa clarify --stop-graceful` typed in a terminal reaches this loop
                # as well — apply's batching lives here, not in the CLI, so this is the
                # only place that can honour it.
                graceful_from_cli = graceful_stop_requested("clarify")
                with run["lock"]:
                    graceful = run["graceful_stop_requested"] or graceful_from_cli
                    if run["stop_requested"] or graceful:
                        stopped = True
                        run["lines"].append(
                            "Apply Answers stopped after the current session finished — "
                            f"{remaining} remaining file(s) were not applied."
                            if graceful else
                            f"Apply Answers stopped — {remaining} remaining file(s) were not applied."
                        )
                        break
                    run["lines"].append(
                        f"{remaining} more fully-answered clarification file(s) still "
                        "need to be applied — running Apply Answers again..."
                    )
                    run["progress"] = None
                returncode = run_once(_CLARIFY_RUN_ARGS["apply"])
                if returncode != 0:
                    break
                next_remaining = _unapplied_answered_count(server)
                if next_remaining >= remaining:
                    # Not making progress (e.g. a file apply can't resolve) — stop
                    # looping rather than spinning forever, and evaluate with
                    # whatever has actually been applied so far.
                    stalled = True
                    with run["lock"]:
                        run["lines"].append(
                            f"Apply Answers isn't clearing the remaining "
                            f"{next_remaining} file(s) — stopping the auto-apply loop. "
                            "Review those file(s) by hand."
                        )
                    break
                remaining = next_remaining
            if returncode == 0 and not stopped and not stalled:
                with run["lock"]:
                    run["lines"].append(
                        "Apply finished. Run Continue Clarification when you want a fresh "
                        "evaluation of the updated PRD."
                    )
        # Safety net: the CLI clears the sentinel itself when it acts on one, but it may
        # have exited for some other reason first (failure, immediate Stop) and left it
        # behind. `graceful_stop_requested` on the run state is deliberately NOT cleared
        # here — the client reads it back to describe how the run ended; the next run's
        # start resets it.
        clear_graceful_stop("clarify")
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["returncode"] = returncode

    threading.Thread(target=worker, daemon=True).start()
    return True


def _stop_clarify_run(server) -> bool:
    """Kill the currently running `tempa clarify` subprocess, whichever mode ("run",
    "finalize", or "apply") is active. Mirrors _stop_implement_run below (same
    `taskkill /T /F` on Windows, to also take out the backend CLI child it spawns, not
    just the immediate process).

    Sets stop_requested regardless of mode: for "apply" it also stops worker()'s
    auto-chain loop from starting its next backlog batch (see _start_clarify_run); for
    "run"/"finalize" it's read by nothing else, since each is already a single Popen
    call and killing that process is the whole story. Returns False if no clarify run
    is currently in progress."""
    run = server.clarify_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["stop_requested"] = True
        process = run["process"]
    if process is None:
        # Nothing to kill right now (e.g. the brief gap before the first Popen call
        # completes, or between apply's auto-chained batches) — `stop_requested` is
        # already set above, which is what actually stops the next apply batch in that
        # second case.
        return True
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            process.terminate()
    except OSError:
        return False
    return True


def _graceful_stop_implement_run(server) -> bool:
    """Ask the running `tempa implement` to stop once the session in progress finishes,
    instead of killing it. Nothing is terminated here — see _graceful_stop_clarify_run
    for why that distinction is the entire feature.

    implement is a separate process, so this can only be a request: it writes the
    sentinel that the runner's poll loop checks between units of work (see
    tempa_implement._graceful_stop_is_due). The runner honours it only once no session
    thread is alive, so it lands after the feature or QA session in flight has finished
    and recorded its work — never in the middle of one. Returns False if nothing is
    running."""
    run = server.implement_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["graceful_stop_requested"] = True
    request_graceful_stop("implement")
    return True


def _cancel_graceful_stop_implement_run(server) -> bool:
    """Withdraw a pending graceful stop, letting the runner carry on. Returns False if
    nothing is running."""
    run = server.implement_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["graceful_stop_requested"] = False
    clear_graceful_stop("implement")
    return True


def _implement_graceful_stop_pending(server) -> bool:
    """Whether a graceful stop is pending for the implement run — from this dashboard or
    from a `tempa implement --stop-graceful` typed in a terminal."""
    run = server.implement_run
    with run["lock"]:
        if run["graceful_stop_requested"]:
            return True
        running = run["running"]
    return running and graceful_stop_requested("implement")


def _graceful_stop_clarify_run(server) -> bool:
    """Ask the running clarify run to stop at its next safe seam instead of killing it.

    The point is the tokens already spent: an immediate Stop takes out the backend CLI
    mid-session, so everything that session had done but not yet written is lost. This
    sets the flags and returns — nothing is ever killed here.

    Where "the next safe seam" is depends on the mode:
      - "apply"    — between the auto-chained backlog batches in _start_clarify_run's
                     worker(), which reads the flag below.
      - "finalize" — between rounds inside `tempa clarify --finalize`, a separate
                     process, which reads the sentinel file instead.
      - "run"      — a single evaluate session with nothing after it, so there is no
                     seam to stop at; not killing it IS the whole effect, and the session
                     gets to record its findings normally.

    The sentinel is written for every mode even though only finalize reads it, so that a
    request made here is visible to `tempa status` and survives a dashboard restart; the
    modes that don't read it clear it when the run ends. Returns False if no clarify run
    is currently in progress."""
    run = server.clarify_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["graceful_stop_requested"] = True
    request_graceful_stop("clarify")
    return True


def _cancel_graceful_stop_clarify_run(server) -> bool:
    """Withdraw a pending graceful stop, letting the run carry on to completion. Returns
    False if no clarify run is currently in progress."""
    run = server.clarify_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["graceful_stop_requested"] = False
    clear_graceful_stop("clarify")
    return True


def _clarify_graceful_stop_pending(server) -> bool:
    """Whether a graceful stop is pending for the clarify run — from this dashboard or
    from a `tempa clarify --stop-graceful` typed in a terminal."""
    run = server.clarify_run
    with run["lock"]:
        if run["graceful_stop_requested"]:
            return True
        running = run["running"]
    return running and graceful_stop_requested("clarify")


# ---------------------------------------------------------------------------
# Implementation run (Start / Stop Implementation) — same subprocess/log-polling
# shape as the clarify run above, but `tempa implement` is a long-running poll loop
# (runs until every epic is done, not one bounded session), so this also tracks the
# live Popen so a Stop button can kill it.
# ---------------------------------------------------------------------------
def _new_implement_run_state() -> dict:
    return {
        "lock": threading.Lock(),
        "running": False,
        "lines": [],
        "progress": None,
        "returncode": None,
        "process": None,
        # Set by _stop_implement_run so the worker knows not to spawn the next child
        # process — a dashboard implement run is two of them back to back (the
        # --reset-failed pass, then implement itself), and Stop pressed during the
        # first one must not be followed by the second one starting anyway.
        "stop_requested": False,
        # Same intent, without the kill — see _graceful_stop_implement_run.
        "graceful_stop_requested": False,
    }


def _start_implement_run(server) -> bool:
    """Start `tempa implement` as a background subprocess, same log-streaming shape
    as _start_clarify_run. Returns False without starting anything if a run is
    already in progress.

    A `tempa implement --reset-failed` pass always runs first (failed → pending). A
    single failed epic makes check_and_run halt immediately without touching anything
    else (see tempa_implement.check_and_run), so without this the dashboard's
    Continue Implementation button would be dead on arrival after any failed session —
    the user's only way forward would be the CLI. The reset is a no-op that logs
    nothing but "No failed sessions found" when nothing is failed, so it's safe to run
    unconditionally: a never-started workspace can't have a failed epic anyway."""
    run = server.implement_run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None
        run["process"] = None
        run["stop_requested"] = False
        run["graceful_stop_requested"] = False
    # See the same call in _start_clarify_run: a stale sentinel must not stop a fresh run.
    clear_graceful_stop("implement")

    def worker() -> None:
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"

        def run_once(args: list[str]) -> int:
            cmd = [sys.executable, str(tempa_py), "implement", *args]
            try:
                process = subprocess.Popen(
                    cmd,
                    # implement's plain run path never calls input() (confirmed: only the
                    # destructive --clear/--clear-plan flags do — --reset-failed only
                    # rewrites statuses in config.json and never prompts) — DEVNULL is
                    # defense in depth, matching the clarify runner, in case that changes.
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
            except OSError as e:
                with run["lock"]:
                    run["lines"].append(f"[error] Could not start implement process: {e}")
                return -1
            # Tracked so Stop Implementation can kill whichever of the two child
            # processes is live at the time (see _stop_implement_run).
            with run["lock"]:
                run["process"] = process
            for raw_line in _read_process_stdout(process):
                line = raw_line.strip()
                if not line:
                    continue
                with run["lock"]:
                    if _PROGRESS_LINE_RE.match(line):
                        run["progress"] = line
                    else:
                        run["lines"].append(line)
            process.wait()
            with run["lock"]:
                run["process"] = None
            return process.returncode

        returncode = run_once(["--reset-failed"])
        # `--reset-failed` rewrites statuses and exits; it never enters the poll loop, so
        # a graceful stop pressed while it ran can only be honoured here, by not starting
        # implement at all. Nothing is lost either way — no session has run yet.
        graceful_from_cli = graceful_stop_requested("implement")
        with run["lock"]:
            if returncode != 0:
                # Never fatal on its own: implement itself still refuses to proceed
                # past a `failed` epic and says so in the log, which is a clearer
                # message than anything this could add.
                run["lines"].append(
                    "Could not reset failed epic(s) back to pending — starting "
                    "implementation anyway."
                )
            stopped = (run["stop_requested"] or run["graceful_stop_requested"]
                       or graceful_from_cli)
            if stopped:
                run["lines"].append("Stopped before implementation started.")
        if not stopped:
            returncode = run_once([])
        # Safety net, same as the clarify worker: the runner clears the sentinel itself
        # when it acts on one, but it may have exited for another reason first.
        clear_graceful_stop("implement")
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["process"] = None
            run["returncode"] = returncode

    threading.Thread(target=worker, daemon=True).start()
    return True


def _stop_implement_run(server) -> bool:
    """Kill the running `tempa implement` subprocess. Uses `taskkill /T /F` on
    Windows to kill its whole process tree — implement spawns the actual backend
    CLI call (claude/copilot/codex) as a child of this same process, and plain
    Popen.terminate() only kills the immediate process, leaving that child running
    (and still burning usage) in the background. Returns False if nothing is
    running."""
    run = server.implement_run
    with run["lock"]:
        if not run["running"]:
            return False
        run["stop_requested"] = True
        process = run["process"]
    if process is None:
        # In between the run's two child processes (--reset-failed → implement):
        # there's nothing to kill right now, and the flag set above is what keeps the
        # worker from spawning the second one.
        return True
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            process.terminate()
    except OSError:
        return False
    return True
