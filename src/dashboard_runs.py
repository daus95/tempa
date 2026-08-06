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
    }


_CLARIFY_RUN_ARGS = {"run": ["--noui"], "finalize": ["--finalize"], "apply": ["--apply"]}


def _max_clarification_run_change_warning(server, previous, current) -> str | None:
    """Warning text for saving a changed "Max Clarification Runs" while a Finalized
    Clarification run is already in progress — or None when there's nothing to warn about.

    `clarify --finalize` reads max_clarification_run ONCE, when its process starts (see
    run_clarify_finalize in tempa_clarify.py), and keeps using that snapshot for its whole
    evaluate/apply loop even though it re-reads the rest of config.json every round. So
    lowering the limit mid-run doesn't shorten the run the user is watching: its rounds keep
    counting toward the limit that was in effect when it started ("ROUND 17/25" while the
    Settings field reads 10), which is easily mistaken for the limit not being enforced at
    all. It is enforced — just from the next finalize run onward. This is the one moment
    that misunderstanding forms, so it's the moment to say so.

    Only mode "finalize" is warned about: it's the only run that reads this setting. Fresh
    per-run subprocesses mean nothing needs restarting for the new value to take effect."""
    if previous == current:
        return None
    run = server.clarify_run
    with run["lock"]:
        if not (run["running"] and run["mode"] == "finalize"):
            return None
    previous_label = f"its previous limit ({previous})" if isinstance(previous, int) else "its original limit"
    return (
        f"Max Clarification Runs was saved as {current}, but a Finalized Clarification run is "
        f"already in progress and will keep using {previous_label} until it stops — it reads "
        "this setting once, when it starts, so the round counter in its log keeps counting "
        "toward the old limit. Your new value applies from the next Finalized Clarification "
        "run onward; nothing needs to be restarted for it to take effect."
    )


def _start_clarify_run(server, mode: str) -> bool:
    """Start `tempa clarify` (mode "run"), `tempa clarify --finalize` (mode "finalize"),
    or `tempa clarify --apply` (mode "apply") as a background subprocess, appending its
    console output to server.clarify_run["lines"] as it streams in. Returns False without
    starting anything if a run is already in progress (defense in depth alongside the
    dashboard disabling the buttons client-side).

    If more than one fully-answered clarification file is still waiting to be applied
    when "apply" is requested, this keeps re-running `clarify --apply` — one file's worth
    of backlog at a time — until every ready file is applied, INSTEAD of chaining to a
    fresh evaluate after only the first one. Only once nothing is left to apply does it
    chain into a fresh "run" (evaluate) pass — see run_once()/worker() below — since
    applying never re-verifies against the live PRD itself, so only a fresh evaluate can
    report what's actually left. Both readiness surfaces are computed from that (the
    finalize-readiness panel via _clarify_finalize_status, and the real Start
    Implementation gate via _implement_readiness_status, both in
    dashboard_clarify_parse.py). Without this, users who only ever click Apply see stale
    critical/major counts with no clear next step."""
    run = server.clarify_run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["mode"] = mode
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None

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
                    # instead auto-chains a fresh evaluate itself — see below — rather
                    # than asking.)
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
            except OSError as e:
                with run["lock"]:
                    run["lines"].append(f"[error] Could not start clarify process: {e}")
                return -1
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                with run["lock"]:
                    if _PROGRESS_LINE_RE.match(line):
                        run["progress"] = line
                    else:
                        run["lines"].append(line)
            process.wait()
            return process.returncode

        returncode = run_once(_CLARIFY_RUN_ARGS[mode])
        if mode == "apply" and returncode == 0:
            remaining = _unapplied_answered_count(server)
            while returncode == 0 and remaining > 0:
                with run["lock"]:
                    run["lines"].append(
                        f"{remaining} more fully-answered clarification file(s) still "
                        "need to be applied — running Apply Answers again before "
                        "evaluating..."
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
                    with run["lock"]:
                        run["lines"].append(
                            f"Apply Answers isn't clearing the remaining "
                            f"{next_remaining} file(s) — stopping the auto-apply loop "
                            "and evaluating with what's been applied so far."
                        )
                    break
                remaining = next_remaining
            if returncode == 0:
                with run["lock"]:
                    run["lines"].append(
                        "Apply finished — automatically starting a new Start Clarification "
                        "run to refresh critical/major status..."
                    )
                    run["mode"] = "run"
                    run["progress"] = None
                returncode = run_once(_CLARIFY_RUN_ARGS["run"])
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["returncode"] = returncode

    threading.Thread(target=worker, daemon=True).start()
    return True


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
            for raw_line in process.stdout:
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
        with run["lock"]:
            if returncode != 0:
                # Never fatal on its own: implement itself still refuses to proceed
                # past a `failed` epic and says so in the log, which is a clearer
                # message than anything this could add.
                run["lines"].append(
                    "Could not reset failed epic(s) back to pending — starting "
                    "implementation anyway."
                )
            stopped = run["stop_requested"]
            if stopped:
                run["lines"].append("Stopped before implementation started.")
        if not stopped:
            returncode = run_once([])
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
