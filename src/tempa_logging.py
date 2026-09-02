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

from tempa_config import get_logs_dir

# The prompt sent to the backend CLI is NOT shown on the console unless the user adds
# --show-prompt. (The prompt is always recorded to the log file regardless.)
SHOW_PROMPT = "--show-prompt" in sys.argv

# The stable middle of the message `_log_reclaimed` writes into both the runner log and the
# session log (tempa_session). Exported because the outcome layer has to RECOGNISE that line
# rather than quote it: `_log_reclaimed` appends it after the agent's last word, so on any
# session that left processes running it IS the tail of the log, and
# `_last_meaningful_log_lines`' fallback would otherwise hand back Tempa's report of its own
# cleanup as the session's explanation of why it stopped — telling the human, in the dashboard's
# Halted panel, that their epic is blocked on "Turn off Terminate leftover processes". Verified
# present at the tail of both incident logs (session_EPIC-02_20260818_154723.txt, 38 processes;
# _20260818_162124.txt, 35). Lives here rather than in tempa_session because
# tempa_session_outcome already imports this module and must never start importing the session
# engine — that edge would constrain every future refactor of the engine, one way, forever.
RECLAIMED_LINE_MARKER = "] the backend CLI exited but left "


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
        # The configured backend/model pair is wrong — either caught before the CLI was
        # spawned (tempa_session.prepare_backend_invocation) or reported by the CLI itself.
        # Deliberately NOT reset per session in _stream_backend_process, exactly like
        # auth_error_hit: it is fatal for the whole process, not for one session.
        self.model_error_hit = False
        self.model_error_message = ""
        self.server_overloaded_hit = False
        self.backend_stuck_after_done_hit = False
        self.background_tasks_terminated_hit = False
        # How many processes this session's own container had to kill on teardown (see
        # `_log_reclaimed`). The only ground truth Tempa has for "the backend left work running
        # and Tempa terminated it" — which is exactly the shape the flag above demonstrably does
        # NOT cover: on 2026-08-18 Claude Code exited 0 with a `dotnet test` of its own about a
        # minute old, printed no marker at all, and the container reclaimed 38 processes.
        # Read only for truthiness, never for magnitude: `_PosixProcessGroup.terminate_tree`
        # returns a literal 0-or-1 rather than a count (`count_is_exact` is False there), so any
        # rule keyed on "how many" would quietly mean something different on Linux than on
        # Windows. Reset per session in `_stream_backend_process`, like the flags above.
        self.reclaimed_process_count = 0
        # The agent's own closing words from the session that just ran — its last piece of plain
        # prose, as opposed to a tool call or a tool result. Captured while streaming because it
        # cannot be recovered from the log file afterwards: a backend renders a tool result as one
        # `[Result] ...` chunk whose own content may span lines, and those continuation lines
        # carry no marker, so a tail scrape of the log has no way to tell "what the agent said"
        # from "what a psql query printed". Reset per session in _stream_backend_process, the same
        # way the flags above are.
        self.last_agent_message = ""

    @property
    def backend_config_error_hit(self) -> bool:
        """A stop that no amount of waiting or retrying can clear: the configured
        credentials, or the configured backend/model pair, are wrong and a human has to
        change them. Unlike a usage limit or a server overload, the next session would fail
        in exactly the same way, so every caller that decides "give up now vs. wait and
        retry" asks this rather than either flag on its own."""
        return self.auth_error_hit or self.model_error_hit
        # Set only by the poll loop itself, and only once it has confirmed no session
        # thread is running — so "the user asked to stop and we reached a clean seam"
        # stays distinguishable from every other reason stop_event gets set (a failure,
        # a usage limit, all epics done), each of which still reports itself normally.
        self.graceful_stop_hit = False


_state = _RunnerState()

# Process-level log file (all log() output goes here)
_process_log_path: Path | None = None
_process_log_lock = threading.Lock()


def _init_process_log() -> None:
    global _process_log_path
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _process_log_path = logs_dir / f"process_{timestamp}.txt"


def process_log_path() -> Path | None:
    """The current process-log file path (None until _init_process_log runs). Callers must
    use this getter, not a direct import of `_process_log_path`, so they see reassignments."""
    return _process_log_path


def _write_to_process_log(line: str) -> None:
    if _process_log_path is None:
        return
    try:
        with _process_log_lock, open(_process_log_path, "a", encoding="utf-8") as f:
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
