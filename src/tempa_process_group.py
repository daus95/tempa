"""Session-scoped process containment: what a session starts, dies with the session.

An agent CLI routinely starts long-running commands — a dev server to check its own work, a
file watcher, a build daemon, a test runner — and does not reliably stop them. Once the CLI
process itself exits, whatever it left behind is orphaned: no longer a descendant of
anything Tempa holds, which is also why `taskkill /T` cannot reach it (that walks *live*
parent-pid links). Seen live in one workspace: a `vite` dev server still holding its port
5.4 hours after the session that started it had finished, and 15 idle MSBuild worker nodes
holding 1.9 GB between them.

The fix is ownership rather than enumeration. Spawn the CLI inside a container the OS tears
down along with the session, and the whole tree goes at once whatever it happens to be made
of. That is what makes this work for a toolchain Tempa has never heard of: it never asks
what a process *is*, only who owns it — so a Gradle daemon, a `vite` server and an MSBuild
node are all handled by the same code, and so is whatever replaces them.

Three implementations behind one interface:

  - **Windows** — a Job Object with KILL_ON_JOB_CLOSE (tempa_process_group_win). The strong
    one: the kernel tears the tree down when our handle closes, so it holds even when Tempa
    is killed outright and no cleanup code of ours ever runs. It also means the dashboard's
    existing `taskkill /PID <tempa implement> /T /F` Stop button starts actually stopping
    the backend's descendants, which today it cannot.
  - **POSIX** — a new session/process group, signalled with `os.killpg`. Cooperative: it
    needs our own cleanup to run, which is why `install_process_cleanup_handlers` below is
    load-bearing here rather than a nicety. There is no POSIX equivalent of
    KILL_ON_JOB_CLOSE; a `SIGKILL` of Tempa still leaks the group.
  - **NullProcessGroup** — what the `terminate_leftover_processes` setting selects when it
    is off. Deliberately restores the *old* spawn rather than being a container that no-ops:
    `popen_kwargs()` returns an empty dict, so `start_new_session` is absent from the Popen
    call entirely and POSIX Ctrl+C semantics are exactly what they were before this module
    existed.

`enabled` is a parameter here, never a config read, so every class in this module is
testable with no config on disk.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading
import time

from tempa_logging import log

# How long a process group gets to honour SIGTERM before SIGKILL. Only the POSIX path uses
# it; a Job Object terminates synchronously and needs no grace period.
_GROUP_KILL_GRACE_SEC = 5.0
_GROUP_POLL_INTERVAL_SEC = 0.1

# Windows has no SIGKILL. Only the POSIX container ever sends this, but the constant is
# resolved here rather than inline so the class stays importable — and drivable by the tests
# — on Windows too.
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class NullProcessGroup:
    """No containment — the spawn and teardown Tempa did before this feature existed.

    Every method is total: none of them raise, so a caller can put `close()` in a `finally`
    without risking replacing the exception already in flight."""

    #: Whether containment is actually in force. False here, and also False on a real group
    #: whose setup failed, so callers have one thing to check.
    active = False
    #: Whether `terminate_tree()`'s return value is an exact process count (Windows) or just
    #: "something was still there" (POSIX). Only affects how the reclaim is logged.
    count_is_exact = False

    def popen_kwargs(self) -> dict:
        return {}

    def adopt(self, process: subprocess.Popen) -> bool:
        return False

    def terminate_tree(self) -> int:
        return 0

    def close(self) -> int:
        return 0


class _PosixProcessGroup(NullProcessGroup):
    """POSIX containment: the child leads a new session, and the whole group is signalled."""

    active = True
    count_is_exact = False

    def __init__(
        self,
        label: str = "",
        *,
        grace_sec: float = _GROUP_KILL_GRACE_SEC,
        poll_interval: float = _GROUP_POLL_INTERVAL_SEC,
        killpg_fn=None,
        getpgrp_fn=None,
        sleep_fn=time.sleep,
    ) -> None:
        # os.killpg/os.getpgrp don't exist on Windows, and a default argument is evaluated
        # when the class body runs — so they're resolved here instead, which also lets the
        # tests drive this class on any platform by injecting fakes.
        self._label = label
        self._pgid: int | None = None
        self._grace_sec = grace_sec
        self._poll_interval = poll_interval
        self._killpg = killpg_fn if killpg_fn is not None else getattr(os, "killpg", None)
        self._getpgrp = getpgrp_fn if getpgrp_fn is not None else getattr(os, "getpgrp", None)
        self._sleep = sleep_fn

    def popen_kwargs(self) -> dict:
        return {"start_new_session": True}

    def adopt(self, process: subprocess.Popen) -> bool:
        """Record the child's process-group id.

        Deliberately `process.pid`, NOT `os.getpgid(process.pid)`. `start_new_session=True`
        makes Python call `setsid()` in the child *after* the fork, so between `Popen`
        returning and a `getpgid` call the child may not have got there yet — and `getpgid`
        would then return **Tempa's own** group id, which a later `killpg` would take out:
        the runner, the dashboard, and the user's shell along with it. POSIX guarantees a
        new session's group id equals its leader's pid, so `process.pid` is both correct and
        immune to that race."""
        self._pgid = process.pid
        _register(self)
        return True

    def terminate_tree(self) -> int:
        pgid, self._pgid = self._pgid, None
        if pgid is None:
            return 0
        # Structural guard against the failure mode described in adopt(): whatever else
        # goes wrong, never signal the group this process is itself in.
        if pgid == 0 or pgid == self._current_group():
            return 0
        if not self._signal(pgid, signal.SIGTERM):
            return 0
        # round(), not int(): 0.3 / 0.1 is 2.9999… in binary floating point, and truncating
        # that would quietly make every grace period one poll shorter than configured.
        for _ in range(max(1, round(self._grace_sec / self._poll_interval))):
            if not self._signal(pgid, 0):
                return 1
            self._sleep(self._poll_interval)
        self._signal(pgid, _SIGKILL)
        return 1

    def close(self) -> int:
        reclaimed = self.terminate_tree()
        _unregister(self)
        return reclaimed

    def _current_group(self) -> int | None:
        if self._getpgrp is None:
            return None
        try:
            return self._getpgrp()
        except OSError:
            return None

    def _signal(self, pgid: int, sig: int) -> bool:
        """Send `sig` to the group; True if the group was still there. Signal 0 is the
        liveness probe."""
        if self._killpg is None:
            return False
        try:
            self._killpg(pgid, sig)
        except OSError:
            # ProcessLookupError (gone) and PermissionError (not ours any more) both mean
            # there is nothing left for us to reclaim.
            return False
        return True


class _WindowsProcessGroup(NullProcessGroup):
    """Windows containment: a Job Object that kills its members when its last handle
    closes."""

    count_is_exact = True

    def __init__(self, label: str = "") -> None:
        import tempa_process_group_win as win

        self._win = win
        self._label = label
        self._job: int | None = None
        self.active = False
        try:
            self._job = win.create_job()
            self.active = True
        except OSError as e:
            self._warn("create a job object for this session", e)

    def popen_kwargs(self) -> dict:
        # Nothing to add: on Windows the process is put into the job *after* it is created.
        # In particular no CREATE_NEW_PROCESS_GROUP — job membership does not affect how
        # console control events are routed, so Ctrl+C behaves exactly as it does today, and
        # adding that flag is what would break it.
        return {}

    def adopt(self, process: subprocess.Popen) -> bool:
        if self._job is None:
            return False
        try:
            self._win.assign_pid(self._job, process.pid)
        except OSError as e:
            self._warn("put this session's processes into a job object", e)
            self._release_handle()
            self.active = False
            return False
        _register(self)
        return True

    def terminate_tree(self) -> int:
        if self._job is None:
            return 0
        # Counted before terminating, so the reclaim message can say what was actually
        # still running rather than making an unfalsifiable claim.
        reclaimed = self._win.active_process_count(self._job)
        self._win.terminate_job(self._job)
        return reclaimed

    def close(self) -> int:
        reclaimed = self.terminate_tree()
        self._release_handle()
        _unregister(self)
        return reclaimed

    def _release_handle(self) -> None:
        if self._job is not None:
            with contextlib.suppress(Exception):
                self._win.close_handle(self._job)
            self._job = None

    def _warn(self, what: str, error: OSError) -> None:
        prefix = f"[{self._label}] " if self._label else ""
        log(f"{prefix}Could not {what} ({error}). This session runs uncontained: anything "
            "the backend CLI leaves running when it exits — a dev server, a build daemon, a "
            "watcher — will not be reclaimed automatically. Everything else is unaffected.")


def make_process_group(enabled: bool, label: str = "") -> NullProcessGroup:
    """The container for one backend-CLI session.

    Returns a plain `NullProcessGroup` when `enabled` is False, or on a platform with no
    implementation — so the off path adds nothing to the `Popen` call and kills nothing.
    Never raises: a session must still run when containment cannot be established."""
    if not enabled:
        return NullProcessGroup()
    try:
        if sys.platform == "win32":
            group = _WindowsProcessGroup(label)
            return group if group.active else NullProcessGroup()
        return _PosixProcessGroup(label)
    except Exception as e:  # noqa: BLE001 — cleanup must never be able to fail a session
        log(f"Could not set up process containment ({e}); this session runs uncontained.")
        return NullProcessGroup()


# --- Live-group registry, for the paths that skip every `finally` --------------------

_live_lock = threading.Lock()
_live: list[NullProcessGroup] = []
_handlers_installed = False


def _register(group: NullProcessGroup) -> None:
    with _live_lock:
        if group not in _live:
            _live.append(group)


def _unregister(group: NullProcessGroup) -> None:
    with _live_lock:
        if group in _live:
            _live.remove(group)


def terminate_live_groups() -> int:
    """Reclaim every session container still open. Total, like `close()` — this runs from
    signal handlers and `atexit`, where raising would be worse than doing nothing."""
    with _live_lock:
        groups = list(_live)
    reclaimed = 0
    for group in groups:
        with contextlib.suppress(Exception):
            reclaimed += group.close()
    return reclaimed


def install_process_cleanup_handlers() -> None:
    """Reclaim every live session container before this process goes away.

    Mandatory on POSIX rather than a nicety: an active container spawns the backend CLI with
    `start_new_session=True`, which takes it out of Tempa's process group, so the terminal
    stops delivering Ctrl+C to it. Without this, Ctrl+C would leave the backend CLI and its
    whole build/test/dev-server tree running — the exact leak this feature exists to close,
    reintroduced through a different door.

    Windows needs no SIGTERM equivalent: the job's KILL_ON_JOB_CLOSE limit means the OS
    tears the tree down when our handle closes, however violently this process dies. It is
    registered for SIGINT anyway so the reclaim is logged the same way on both platforms.

    Idempotent, and a silent no-op off the main thread (where `signal.signal` raises)."""
    global _handlers_installed
    if _handlers_installed:
        return
    _handlers_installed = True

    atexit.register(terminate_live_groups)

    def _on_signal(signum, frame):
        terminate_live_groups()
        # Restore whatever was there before and re-raise, so exit status and traceback are
        # exactly what they would have been without this handler in the way.
        signal.signal(signum, previous.get(signum, signal.SIG_DFL))
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        os.kill(os.getpid(), signum)

    previous: dict[int, object] = {}
    signums = [signal.SIGINT] if sys.platform == "win32" else [signal.SIGINT, signal.SIGTERM]
    for signum in signums:
        with contextlib.suppress(ValueError, OSError, AttributeError):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _on_signal)
