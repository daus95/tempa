"""Windows process containment via a Job Object (ctypes / kernel32).

Used only on Windows, but written so it **imports cleanly everywhere**: the struct
definitions below are the highest-risk code in this feature — a wrong field type silently
corrupts memory rather than failing loudly — and CI runs on ubuntu only. Keeping the module
importable means `tests/test_tempa_process_group.py` can assert every struct's size and
field offsets on every push instead of never. That is why the types below are spelled with
plain `ctypes` primitives rather than `ctypes.wintypes` (which raises on import off Windows)
and why the `WinDLL` handle is created lazily instead of at import time.

Note this is in-process interop, which `dashboard_winui` deliberately avoids (it shells out
to PowerShell for its folder picker rather than driving WinForms through ctypes). The
exception is justified here and cannot be worked around: a Job Object ties its processes'
lifetime to an open *handle*, so the owner must be the process holding it, and a handle
cannot be handed to a subprocess. That handle is also what makes this survive Tempa being
killed outright — when the process dies the OS closes the handle and the kernel terminates
everything still in the job, with no cleanup code of ours needing to run."""

from __future__ import annotations

import ctypes
from functools import lru_cache

# Win32 primitives, spelled without ctypes.wintypes so this module imports off Windows too
# (see the module docstring). SIZE_T/ULONG_PTR are pointer-width — using DWORD for them is
# the classic mistake that silently shifts every later field on x64.
_HANDLE = ctypes.c_void_p
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int
_UINT = ctypes.c_uint
_LPVOID = ctypes.c_void_p
_LPCWSTR = ctypes.c_wchar_p
_SIZE_T = ctypes.c_size_t
_ULONG_PTR = ctypes.c_size_t
_LARGE_INTEGER = ctypes.c_int64
_ULONGLONG = ctypes.c_uint64

# JOBOBJECTINFOCLASS values (winnt.h).
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags — the whole point of this module: every
# process still in the job is terminated when the last handle to it closes, including when
# Tempa is killed outright, which is exactly when an atexit hook would never run.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# OpenProcess access rights.
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_ERROR_ACCESS_DENIED = 5


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", _ULONGLONG),
        ("WriteOperationCount", _ULONGLONG),
        ("OtherOperationCount", _ULONGLONG),
        ("ReadTransferCount", _ULONGLONG),
        ("WriteTransferCount", _ULONGLONG),
        ("OtherTransferCount", _ULONGLONG),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    # x64 layout: LimitFlags at offset 16, Affinity at 48, sizeof == 64. ctypes' default
    # alignment reproduces MSVC's default packing here, so no _pack_ is wanted — the sizes
    # and offsets are pinned by tests rather than by asserts, which `python -O` would strip.
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", _ULONG_PTR),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", _LARGE_INTEGER),
        ("TotalKernelTime", _LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", _LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", _LARGE_INTEGER),
        ("TotalPageFaultCount", _DWORD),
        ("TotalProcesses", _DWORD),
        ("ActiveProcesses", _DWORD),
        ("TotalTerminatedProcesses", _DWORD),
    ]


@lru_cache(maxsize=1)
def _kernel32():
    """The kernel32 handle, with every prototype declared. Created on first use rather than
    at import so this module still imports off Windows (see the module docstring).

    A private `WinDLL` instance, never `ctypes.windll.kernel32`: the latter is a
    process-global cached object, so setting `.argtypes` on it would mutate state shared
    with every other library in this process. `use_last_error=True` pairs with
    `ctypes.get_last_error()`, which snapshots the error at the moment of the call instead
    of re-reading a thread-local that any later call can clobber.

    Explicit argtypes/restype on all of them is not optional: without a `restype`, ctypes
    truncates a returned HANDLE to a 32-bit int on x64 — an intermittent corruption that
    only shows up once the OS hands out a handle above 2**31."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [_LPVOID, _LPCWSTR]
    k.CreateJobObjectW.restype = _HANDLE
    k.SetInformationJobObject.argtypes = [_HANDLE, ctypes.c_int, _LPVOID, _DWORD]
    k.SetInformationJobObject.restype = _BOOL
    k.QueryInformationJobObject.argtypes = [
        _HANDLE, ctypes.c_int, _LPVOID, _DWORD, ctypes.POINTER(_DWORD),
    ]
    k.QueryInformationJobObject.restype = _BOOL
    k.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    k.AssignProcessToJobObject.restype = _BOOL
    k.IsProcessInJob.argtypes = [_HANDLE, _HANDLE, ctypes.POINTER(_BOOL)]
    k.IsProcessInJob.restype = _BOOL
    k.OpenProcess.argtypes = [_DWORD, _BOOL, _DWORD]
    k.OpenProcess.restype = _HANDLE
    k.TerminateJobObject.argtypes = [_HANDLE, _UINT]
    k.TerminateJobObject.restype = _BOOL
    k.CloseHandle.argtypes = [_HANDLE]
    k.CloseHandle.restype = _BOOL
    return k


def create_job() -> int:
    """Create an unnamed Job Object that kills everything still in it when its last handle
    closes. Raises OSError if either call fails, so the caller can fall back to running
    uncontained rather than believing it has containment it doesn't have.

    Both NULLs matter. No security attributes means the handle is **not inheritable** —
    an inherited copy in a child would keep the job alive past our own exit, and
    KILL_ON_JOB_CLOSE only fires when the *last* handle closes, so inheritance would defeat
    the crash-safety this exists for. No name means anonymous: two Tempa processes (a
    dashboard-driven clarify and an implement run) must never collide on a shared job."""
    k = _kernel32()
    handle = k.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k.SetInformationJobObject(
        handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info), ctypes.sizeof(info),
    )
    if not ok:
        error = ctypes.get_last_error()
        k.CloseHandle(handle)
        raise ctypes.WinError(error)
    return handle


def assign_pid(job_handle: int, pid: int) -> None:
    """Put an already-running process — and everything it spawns from now on — into the job.

    Opens the process by pid rather than reaching into `Popen._handle`, so this module stays
    independent of a private CPython attribute. That is not the race it looks like: `Popen`
    keeps its own handle to the child open for the child's whole lifetime, which reserves
    the pid, so the number cannot be recycled underneath us.

    Raises OSError on failure. The one worth recognising is ERROR_ACCESS_DENIED (5), which
    is what a job-nesting restriction looks like — job nesting is supported from Windows 8
    onward, so this is rare, but it is why every caller must be able to carry on
    uncontained."""
    k = _kernel32()
    process_handle = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not process_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not k.AssignProcessToJobObject(job_handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())
        # Trust but verify: the assignment is racing the child's own first CreateProcess
        # call (see tempa_process_group.SessionProcessGroup.adopt), and a silent miss would
        # leave us reporting containment we don't have.
        inside = _BOOL(0)
        if k.IsProcessInJob(process_handle, job_handle, ctypes.byref(inside)) and not inside.value:
            raise OSError("AssignProcessToJobObject reported success but the process is not in the job")
    finally:
        # The job holds its own reference to the process; this handle was only needed to
        # name it.
        k.CloseHandle(process_handle)


def active_process_count(job_handle: int) -> int:
    """How many processes are still alive in the job — read just before terminating it, so
    the log line can say what was actually reclaimed. Returns 0 rather than raising if the
    query fails: a diagnostic number is never worth failing a teardown over."""
    k = _kernel32()
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = _DWORD(0)
    ok = k.QueryInformationJobObject(
        job_handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
    )
    return int(info.ActiveProcesses) if ok else 0


def terminate_job(job_handle: int) -> None:
    """Kill every process still in the job. Closing the handle would do this too (that is
    the KILL_ON_JOB_CLOSE limit), but terminating explicitly keeps the normal path from
    depending on exactly when CPython releases the handle."""
    _kernel32().TerminateJobObject(job_handle, 1)


def close_handle(job_handle: int) -> None:
    _kernel32().CloseHandle(job_handle)
