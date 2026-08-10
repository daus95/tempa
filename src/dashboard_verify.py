"""Verification runs (subprocess + live log polling), dashboard side.

Spawns `tempa.py verify <epic>` as a child process per epic (unlike clarify/implement,
verify allows more than one epic to run at once — see `server.verify_runs`, a dict keyed
by epic name rather than a single global slot) and streams its console output the same way
`dashboard_runs.py` does for clarify/implement. Also builds the combined "list of
verification runs" the Verification page shows (in-flight/failed runs tracked in memory,
finished ones read back from the `.tempa/verify/*.md` report files on disk) and reads/
deletes individual reports.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

import tempa_config
from dashboard_spec import _resolve_within
from tempa_session import _read_process_stdout

# Matches the "<epic>-verify-<timestamp>.md" filename convention written by run_verify()
# (tempa_commands.py). Anchored so an epic name that happens to contain "-verify-" itself
# doesn't confuse the split — the timestamp suffix's fixed digit shape is the anchor.
_REPORT_NAME_RE = re.compile(r"^(?P<epic>.+)-verify-(?P<ts>\d{8}_\d{6})\.md$")

# Extracts the machine-readable result counts verify.md instructs the AI to write as the
# report's first line. Older reports (written before this marker existed) or a session that
# didn't follow the instruction simply won't match — callers treat that as "unknown", not
# an error.
_RESULT_MARKER_RE = re.compile(
    r"<!--\s*tempa:verify-result\s+passed=(\d+)\s+warned=(\d+)\s+failed=(\d+)\s*-->"
)


def _new_verify_run_state() -> dict:
    return {
        "lock": threading.Lock(),
        "running": False,
        "lines": [],
        "progress": None,
        "returncode": None,
        "process": None,
        "stop_requested": False,
        # Filename of the report this run produced, filled in by the worker once the
        # subprocess exits 0 — lets _list_verify_runs hand off to the file-based row
        # without also showing a stale "live" duplicate for the same run.
        "report_file": None,
    }


def _start_verify_run(server, epic: str) -> bool:
    """Start `tempa.py verify <epic>` as a background subprocess for this epic. Returns
    False without starting anything if a verify run for this SAME epic is already in
    progress — different epics are independent and may run concurrently (each gets its own
    entry in server.verify_runs), unlike clarify/implement's single global run slot."""
    run = server.verify_runs.get(epic)
    if run is None:
        run = _new_verify_run_state()
        server.verify_runs[epic] = run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None
        run["process"] = None
        run["stop_requested"] = False
        run["report_file"] = None

    def worker() -> None:
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "verify", epic]
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as e:
            with run["lock"]:
                run["lines"].append(f"[error] Could not start verify process: {e}")
                run["running"] = False
                run["returncode"] = -1
            return
        with run["lock"]:
            run["process"] = process
        for raw_line in _read_process_stdout(process):
            line = raw_line.strip()
            if not line:
                continue
            with run["lock"]:
                run["lines"].append(line)
        process.wait()
        returncode = process.returncode
        report_file = None
        if returncode == 0:
            matches = sorted(
                tempa_config.get_verify_dir().glob(f"{epic}-verify-*.md"),
                key=lambda p: p.stat().st_mtime,
            )
            if matches:
                report_file = matches[-1].name
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["process"] = None
            run["returncode"] = returncode
            run["report_file"] = report_file

    threading.Thread(target=worker, daemon=True).start()
    return True


def _stop_verify_run(server, epic: str) -> bool:
    """Kill the running verify subprocess for `epic`. Returns False if that epic has no
    verify run currently in progress. Mirrors _stop_implement_run's process-tree kill on
    Windows (verify spawns the actual backend CLI as a child, same as implement)."""
    run = server.verify_runs.get(epic)
    if run is None:
        return False
    with run["lock"]:
        if not run["running"]:
            return False
        run["stop_requested"] = True
        process = run["process"]
    if process is None:
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


def _parse_verify_result(content: str) -> dict | None:
    """Extract {"passed", "warned", "failed"} counts from the report's leading
    tempa:verify-result marker, or None if it's missing (pre-marker report, or the AI
    omitted it — the caller shows "unknown" rather than guessing)."""
    m = _RESULT_MARKER_RE.search(content)
    if not m:
        return None
    passed, warned, failed = (int(g) for g in m.groups())
    return {"passed": passed, "warned": warned, "failed": failed}


def _result_label(counts: dict | None) -> str | None:
    if counts is None:
        return None
    return "passed" if counts["warned"] == 0 and counts["failed"] == 0 else "issues"


def _format_timestamp(ts: str) -> str:
    """"20260810_143022" -> "2026-08-10 14:30:22" for display."""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"


def _list_verify_runs(server) -> list[dict]:
    """Combined list of verification runs: finished reports on disk (status "completed",
    newest first) plus in-memory rows for whatever's currently running or most recently
    failed per epic. A successfully finished run is represented ONLY by its file-based row
    (see report_file in _start_verify_run) — it's dropped from the live dict's contribution
    here to avoid listing the same run twice."""
    rows = []
    verify_dir = tempa_config.get_verify_dir()
    if verify_dir.is_dir():
        for path in sorted(verify_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = _REPORT_NAME_RE.match(path.name)
            if not m:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            rows.append({
                "id": path.name,
                "epic": m.group("epic"),
                "timestamp": _format_timestamp(m.group("ts")),
                "sortKey": m.group("ts"),
                "status": "completed",
                "result": _result_label(_parse_verify_result(content)),
            })

    for epic, run in server.verify_runs.items():
        with run["lock"]:
            running = run["running"]
            returncode = run["returncode"]
        if running:
            rows.append({
                "id": "live:" + epic, "epic": epic, "timestamp": "",
                "sortKey": "9" * 15,  # always sorts above any real timestamp
                "status": "running", "result": None,
            })
        elif returncode is not None and returncode != 0:
            rows.append({
                "id": "live:" + epic, "epic": epic, "timestamp": "",
                "sortKey": "9" * 15,
                "status": "failed", "result": None,
            })

    rows.sort(key=lambda r: r["sortKey"], reverse=True)
    for r in rows:
        del r["sortKey"]
    return rows


def _verify_detail(server, run_id: str) -> dict | None:
    """Detail payload for one verification run. For a file-based id, returns the rendered
    report's raw markdown content. For a "live:<epic>" id (still running, or failed with no
    report produced), returns the live status instead — the detail page renders a status
    message + Stop button rather than a markdown viewer in that case."""
    if run_id.startswith("live:"):
        epic = run_id[len("live:"):]
        run = server.verify_runs.get(epic)
        if run is None:
            return None
        with run["lock"]:
            running = run["running"]
            returncode = run["returncode"]
        return {
            "id": run_id, "epic": epic, "timestamp": "",
            "status": "running" if running else "failed",
            "result": None, "content": None,
        }
    target = _resolve_within(tempa_config.get_verify_dir(), run_id)
    if target is None or target.suffix.lower() != ".md" or not target.is_file():
        return None
    m = _REPORT_NAME_RE.match(target.name)
    if not m:
        return None
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {
        "id": run_id, "epic": m.group("epic"), "timestamp": _format_timestamp(m.group("ts")),
        "status": "completed", "result": _result_label(_parse_verify_result(content)),
        "content": content,
    }


def _delete_verify_run(run_id: str) -> bool:
    """Delete one verification report from disk. Only valid for file-based ids — a
    "live:<epic>" id has no file yet, so there's nothing to delete."""
    if run_id.startswith("live:"):
        return False
    target = _resolve_within(tempa_config.get_verify_dir(), run_id)
    if target is None or target.suffix.lower() != ".md" or not target.is_file():
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True
