"""Tests for session process containment (src/tempa_process_group.py).

Same scope rule as test_tempa_session.py: anything that actually spawns a process is out of
scope here. Every class in tempa_process_group takes its OS primitives as injectable
parameters precisely so it can be driven against fakes instead — which also means the POSIX
container is exercised on Windows and vice versa, rather than half the module going untested
on whichever platform CI happens to run.

The struct-layout tests are the reason tempa_process_group_win imports off Windows at all.
They are the highest-value tests in this file: a wrong ctypes field type there silently
shifts every later field and corrupts memory rather than failing, and CI runs on ubuntu, so
without them that code would never be checked by anything."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from unittest.mock import Mock

import pytest

import tempa_process_group as tpg
import tempa_process_group_win as tpgw

IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8


# ---------------------------------------------------------------------------
# Job Object struct layout — runs on every platform, which is the whole point
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not IS_64BIT, reason="the pinned sizes below are the x64 layout")
@pytest.mark.parametrize("struct,expected_size", [
    (tpgw.IO_COUNTERS, 48),
    (tpgw.JOBOBJECT_BASIC_LIMIT_INFORMATION, 64),
    (tpgw.JOBOBJECT_EXTENDED_LIMIT_INFORMATION, 144),
    (tpgw.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, 48),
])
def test_job_object_structs_match_the_win32_layout(struct, expected_size):
    assert ctypes.sizeof(struct) == expected_size


@pytest.mark.skipif(not IS_64BIT, reason="the pinned offsets below are the x64 layout")
def test_job_object_struct_field_offsets_match_the_win32_layout():
    """Sizes alone can't catch a pair of same-width fields being swapped, and using DWORD
    where the header says SIZE_T shifts everything after it. These two offsets are where
    that would show up."""
    assert tpgw.JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags.offset == 16
    assert tpgw.JOBOBJECT_EXTENDED_LIMIT_INFORMATION.IoInfo.offset == 64


def test_kill_on_job_close_is_the_documented_flag_value():
    assert tpgw.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000


# ---------------------------------------------------------------------------
# The off path
# ---------------------------------------------------------------------------

def test_disabled_adds_nothing_to_the_spawn():
    """The load-bearing assertion for the toggle: with containment off, popen_kwargs is
    empty, so `start_new_session` is absent from the Popen call rather than passed as False
    — which is what keeps POSIX Ctrl+C semantics exactly as they were."""
    assert tpg.make_process_group(False).popen_kwargs() == {}


def test_disabled_group_never_touches_the_process():
    group = tpg.make_process_group(False)
    process = Mock()
    assert group.adopt(process) is False
    assert group.active is False
    assert group.terminate_tree() == 0
    assert group.close() == 0
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


# ---------------------------------------------------------------------------
# POSIX container, driven against injected primitives on any platform
# ---------------------------------------------------------------------------

def _posix_group(**overrides):
    defaults = {
        "grace_sec": 0.3,
        "poll_interval": 0.1,
        "killpg_fn": Mock(),
        "getpgrp_fn": lambda: 999,
        "sleep_fn": Mock(),
    }
    defaults.update(overrides)
    return tpg._PosixProcessGroup("label", **defaults), defaults


def test_posix_group_starts_a_new_session():
    group, _ = _posix_group()
    assert group.popen_kwargs() == {"start_new_session": True}
    assert group.active is True


def test_posix_group_takes_the_pgid_from_the_pid_never_from_getpgid(monkeypatch):
    """`start_new_session=True` makes Python call setsid() in the child AFTER the fork, so
    between Popen returning and a getpgid() call the child may not have got there — and
    getpgid would then answer with TEMPA'S OWN group, which a later killpg would take out:
    runner, dashboard and the user's shell together. POSIX guarantees a new session's pgid
    equals its leader's pid, so pid is both correct and immune to the race. This test fails
    loudly if anyone ever 'corrects' it back to getpgid."""
    def poison(*args, **kwargs):
        raise AssertionError("adopt() must not call os.getpgid — see the docstring")

    monkeypatch.setattr(os, "getpgid", poison, raising=False)
    group, deps = _posix_group()
    group.adopt(Mock(pid=4242))
    group.terminate_tree()
    assert deps["killpg_fn"].call_args_list[0][0][0] == 4242


def test_posix_group_refuses_to_signal_its_own_process_group():
    """Structural guard: whatever else goes wrong, never signal the group this process is
    itself in."""
    group, deps = _posix_group(getpgrp_fn=lambda: 4242)
    group.adopt(Mock(pid=4242))
    assert group.terminate_tree() == 0
    deps["killpg_fn"].assert_not_called()


def test_posix_group_escalates_from_sigterm_to_sigkill_when_the_group_survives():
    group, deps = _posix_group()
    group.adopt(Mock(pid=4242))
    assert group.terminate_tree() == 1
    signals = [call[0][1] for call in deps["killpg_fn"].call_args_list]
    assert signals[0] == signal.SIGTERM
    assert signals[-1] == tpg._SIGKILL  # signal.SIGKILL, or SIGTERM where there isn't one
    assert deps["sleep_fn"].call_count == 3  # grace_sec 0.3 / poll_interval 0.1


def test_posix_group_stops_at_sigterm_when_the_group_is_already_gone():
    killpg = Mock(side_effect=[None, ProcessLookupError()])
    group, _ = _posix_group(killpg_fn=killpg)
    group.adopt(Mock(pid=4242))
    assert group.terminate_tree() == 1
    assert [call[0][1] for call in killpg.call_args_list] == [signal.SIGTERM, 0]


def test_posix_group_reports_nothing_reclaimed_when_the_group_died_on_its_own():
    group, _ = _posix_group(killpg_fn=Mock(side_effect=ProcessLookupError()))
    group.adopt(Mock(pid=4242))
    assert group.terminate_tree() == 0


def test_posix_group_close_is_idempotent():
    group, deps = _posix_group(killpg_fn=Mock(side_effect=ProcessLookupError()))
    group.adopt(Mock(pid=4242))
    group.close()
    deps["killpg_fn"].reset_mock()
    assert group.close() == 0
    deps["killpg_fn"].assert_not_called()


def test_close_never_raises_even_when_the_kill_primitive_explodes():
    """close() runs inside a `finally`; an exception there would REPLACE whatever error was
    already in flight, so the caller would see a teardown failure instead of the real one."""
    group, _ = _posix_group(killpg_fn=Mock(side_effect=RuntimeError("boom")))
    group.adopt(Mock(pid=4242))
    with pytest.raises(RuntimeError):
        group.terminate_tree()  # the raw primitive does propagate...
    assert tpg.terminate_live_groups() == 0  # ...but the registry sweep swallows it


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_live_groups_are_registered_on_adopt_and_released_on_close():
    group, _ = _posix_group(killpg_fn=Mock(side_effect=ProcessLookupError()))
    group.adopt(Mock(pid=4242))
    assert group in tpg._live
    group.close()
    assert group not in tpg._live


def test_terminate_live_groups_reclaims_everything_still_open():
    first, _ = _posix_group()
    second, _ = _posix_group()
    first.adopt(Mock(pid=4242))
    second.adopt(Mock(pid=4243))
    try:
        assert tpg.terminate_live_groups() == 2
        assert first not in tpg._live and second not in tpg._live
    finally:
        tpg._unregister(first)
        tpg._unregister(second)


@pytest.mark.skipif(sys.platform == "win32", reason="a disabled group is what Windows gets here")
def test_enabled_group_on_posix_is_a_real_container():
    assert tpg.make_process_group(True).popen_kwargs() == {"start_new_session": True}
