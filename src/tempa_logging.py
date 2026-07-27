"""Cross-thread runner state, process logging, and console-output helpers.

Holds the single shared `_state` (the `_RunnerState` used by the poll loop and every
session thread) and the process-wide log file. Everything that runs a session imports
`_state`, `log`, and the banner/hyperlink helpers from here.

Import by reference: `_state` is a mutable object shared across modules (mutate its
attributes, never rebind the name). The process-log path is private and reassigned by
`_init_process_log()`, so read it through `process_log_path()` rather than importing the
name (a plain `from tempa_logging import _process_log_path` would freeze the old value).
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

from tempa_config import LOGS_DIR

# The prompt sent to Claude is NOT shown on the console unless the user adds
# --show-prompt. (The prompt is always recorded to the log file regardless.)
SHOW_PROMPT = "--show-prompt" in sys.argv


class _RunnerState:
    """Mutable cross-thread state shared by the poll loop (check_and_run) and whichever
    session thread is currently running. Grouped into one object instead of loose globals
    so every read/write site is explicit about what it's touching; `lock` guards
    `running_thread`/`running_index` plus every read-modify-write of config.json."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running_thread: threading.Thread | None = None
        self.running_index: int | None = None
        self.stop_event = threading.Event()
        self.all_done = False
        self.usage_limit_hit = False
        self.auth_error_hit = False
        self.auth_error_message = ""


_state = _RunnerState()

# Process-level log file (all log() output goes here)
_process_log_path: Path | None = None
_process_log_lock = threading.Lock()


def _init_process_log() -> None:
    global _process_log_path
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _process_log_path = LOGS_DIR / f"process_{timestamp}.txt"


def process_log_path() -> Path | None:
    """The current process-log file path (None until _init_process_log runs). Callers must
    use this getter, not a direct import of `_process_log_path`, so they see reassignments."""
    return _process_log_path


def _write_to_process_log(line: str) -> None:
    if _process_log_path is None:
        return
    try:
        with _process_log_lock:
            with open(_process_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def log(message: str, to_console: bool = True) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    if to_console:
        print(line, flush=True)
    _write_to_process_log(line)


def _banner(title: str) -> None:
    """Print a single-line banner (replaces the old multi-line '=' separator blocks)."""
    print(f"== {title} ==", flush=True)


def _print_log_tail(log_path: Path, lines: int = 20) -> None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = text.strip().splitlines()[-lines:]
        log(f"--- last {lines} lines of {log_path.name} ---")
        for line in tail:
            print(f"  {line}", flush=True)
        log("--- end ---")
    except Exception as e:
        log(f"Could not read log file: {e}")


def _hyperlink(path: Path) -> str:
    """Wrap an absolute path in an OSC 8 terminal hyperlink (file:// URI) so terminals
    that support it (e.g. Windows Terminal) render it clickable — Ctrl+Click opens the
    file. Falls back to the plain path when stdout is not a TTY (piped/redirected) or the
    path cannot be turned into a URI."""
    text = str(path)
    try:
        if not sys.stdout.isatty():
            return text
        uri = Path(path).as_uri()  # requires an absolute path; percent-encodes
    except (ValueError, AttributeError):
        return text
    esc = "\033"
    return f"{esc}]8;;{uri}{esc}\\{text}{esc}]8;;{esc}\\"
