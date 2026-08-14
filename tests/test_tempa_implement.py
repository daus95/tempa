"""Tests for tempa_implement.py — currently the recovery path that runs when the backend's
API reported itself overloaded (a transient 529): the wait-then-retry in `main` must clear a
leftover `failed` epic status first, or check_and_run would refuse to resume anything.

No backend process is ever spawned here: `check_and_run` and `wait_out_server_overload` are
stubbed so the poll loop's control flow can be driven directly.
"""

from __future__ import annotations

import threading
import time

import pytest

import tempa_config
import tempa_implement as ti
from tempa_logging import _state


@pytest.fixture(autouse=True)
def reset_runner_state():
    """`_state` is a process-wide singleton — reset the flags the poll loop branches on so
    one test's leftover stop/overload flag can't leak into the next."""
    yield
    _state.stop_event.clear()
    _state.all_done = False
    _state.usage_limit_hit = False
    _state.auth_error_hit = False
    _state.server_overloaded_hit = False
    _state.backend_stuck_after_done_hit = False
    _state.running_thread = None
    _state.running_index = None


# ---------------------------------------------------------------------------
# _reset_failed_before_retry
# ---------------------------------------------------------------------------

def test_reset_failed_before_retry_flips_failed_to_pending(isolate_tempa_paths):
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed", "session_id": "abc"},
        {"epic_name": "e2", "status": "pending"},
    ]})

    ti._reset_failed_before_retry("Implementation")

    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert "session_id" not in saved["epic"][0]
    assert saved["epic"][1]["status"] == "pending"


def test_reset_failed_before_retry_no_failed_epic_leaves_config_untouched(isolate_tempa_paths):
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "on_progress"}]})
    before = tempa_config.get_config_path().read_text(encoding="utf-8")

    ti._reset_failed_before_retry("Implementation")

    assert tempa_config.get_config_path().read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# main() — the overload wait-then-retry branch
# ---------------------------------------------------------------------------

def _drive_poll_loop(monkeypatch, *, first_stop: str) -> dict:
    """Stub out check_and_run + wait_out_server_overload so main()'s loop runs twice: the
    first poll stops the runner the way `first_stop` says ("overload"/"failure"), the second
    reports everything done (exit 0). Returns a dict recording what happened."""
    seen: dict = {"polls": 0, "waits": []}

    def fake_check_and_run(features_override: int | None = None) -> None:
        seen["polls"] += 1
        if seen["polls"] == 1 and first_stop == "overload":
            _state.server_overloaded_hit = True
        elif seen["polls"] > 1:
            _state.all_done = True
        _state.stop_event.set()

    def fake_wait_out_server_overload(label: str, attempt: int) -> None:
        seen["waits"].append((label, attempt))
        _state.server_overloaded_hit = False
        _state.stop_event.clear()

    monkeypatch.setattr(ti, "check_and_run", fake_check_and_run)
    monkeypatch.setattr(ti, "wait_out_server_overload", fake_wait_out_server_overload)
    return seen


def test_main_resets_failed_epic_after_overload_wait(isolate_tempa_paths, monkeypatch):
    # e2 is on_progress so there IS pending work (no plan run); e1 was left `failed` by the
    # session the overload cut short — without the reset, the retry's very first
    # check_and_run would halt on it.
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed", "session_id": "abc"},
        {"epic_name": "e2", "status": "on_progress"},
    ]})
    seen = _drive_poll_loop(monkeypatch, first_stop="overload")

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert exc.value.code == 0
    assert seen["waits"] == [("Implementation", 1)]
    assert seen["polls"] == 2  # the retry actually got to poll again
    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert saved["epic"][1]["status"] == "on_progress"


def test_main_plain_session_failure_keeps_failed_status(isolate_tempa_paths, monkeypatch):
    # A real (non-overload) failure must stay `failed` and stop the runner with exit 1 —
    # the reset is only for the transient-overload retry path.
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed"},
        {"epic_name": "e2", "status": "on_progress"},
    ]})
    seen = _drive_poll_loop(monkeypatch, first_stop="failure")

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert exc.value.code == 1
    assert seen["waits"] == []
    assert tempa_config.load_config()["epic"][0]["status"] == "failed"


def test_main_resumes_automatically_after_backend_stuck_after_done_hit(isolate_tempa_paths, monkeypatch):
    # Mirrors the overload wait-then-retry test above: not a real failure, so main() clears
    # the flag and keeps polling instead of stopping the runner.
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "on_progress"}]})
    seen = {"polls": 0}

    def fake_check_and_run(features_override: int | None = None) -> None:
        seen["polls"] += 1
        if seen["polls"] == 1:
            _state.backend_stuck_after_done_hit = True
        else:
            _state.all_done = True
        _state.stop_event.set()

    monkeypatch.setattr(ti, "check_and_run", fake_check_and_run)

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert exc.value.code == 0
    assert seen["polls"] == 2  # the retry actually got to poll again
    assert _state.backend_stuck_after_done_hit is False


def test_main_waits_for_the_session_thread_before_reading_the_stop_flags(
    isolate_tempa_paths, monkeypatch,
):
    """The live regression: the watchdog raises stop_event from a daemon thread, but the
    session thread only reaches its own bookkeeping seconds later (stdout drain + wait). If
    main() reads and clears the flags in that window, run_session sees a plain non-zero exit
    and marks the epic `failed` — turning a deliberate not-a-failure into exit 1."""
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "on_progress"}]})
    seen = {"polls": 0, "flag_seen_by_session_thread": None}

    def fake_session_thread() -> None:
        # Mimic _terminate_if_stuck_after_done: raise the stop first, act on it only later.
        _state.stop_event.set()
        time.sleep(0.2)
        _state.backend_stuck_after_done_hit = True
        seen["flag_seen_by_session_thread"] = _state.backend_stuck_after_done_hit

    def fake_check_and_run(features_override: int | None = None) -> None:
        seen["polls"] += 1
        if seen["polls"] == 1:
            _state.running_thread = threading.Thread(target=fake_session_thread, daemon=True)
            _state.running_thread.start()
        else:
            _state.all_done = True
            _state.stop_event.set()

    monkeypatch.setattr(ti, "check_and_run", fake_check_and_run)

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert seen["flag_seen_by_session_thread"] is True
    assert exc.value.code == 0  # resumed, not stopped as a failure
    assert seen["polls"] == 2


def test_await_session_thread_returns_immediately_with_no_running_thread():
    _state.running_thread = None
    ti._await_session_thread(timeout_sec=0.01)  # must not raise


def test_await_session_thread_gives_up_on_a_wedged_thread(monkeypatch):
    # A genuinely wedged session thread must not hang the runner forever.
    release = threading.Event()
    _state.running_thread = threading.Thread(target=release.wait, daemon=True)
    _state.running_thread.start()
    try:
        ti._await_session_thread(timeout_sec=0.05)
        assert _state.running_thread.is_alive()  # gave up rather than blocking
    finally:
        release.set()


# ---------------------------------------------------------------------------
# check_and_run — resuming an epic's previous implementation session
#
# A continuation session (an epic that already has completed_features > 0, is
# require_fixing, or is a stale on_progress left by an interrupted session) used to
# always start `run_session` cold, even though the epic's previous session_id was
# already captured and stored (see tempa_session._capture_session_id) — resume_session_id
# was dead code for implementation. check_and_run now looks that id up (via
# get_epic_session_id) and passes it through, so the epic spec + code it already read
# don't get re-read from scratch every features_per_session batch. run_session itself is
# stubbed here — these tests are only about what check_and_run decides to pass it.
# ---------------------------------------------------------------------------

def _run_check_and_run_capturing_resume(monkeypatch) -> dict:
    """Stub ti.run_session to record the resume_session_id it was called with (instead of
    spawning a real backend session), drive check_and_run once, and wait for the daemon
    thread it starts to finish."""
    seen: dict = {}

    def fake_run_session(index, prompt, session_label, resume_session_id=None, features_override=None):
        seen["resume_session_id"] = resume_session_id
        seen["session_label"] = session_label

    monkeypatch.setattr(ti, "run_session", fake_run_session)
    ti.check_and_run()
    assert _state.running_thread is not None, "check_and_run didn't start a session thread"
    _state.running_thread.join(timeout=5)
    return seen


def test_check_and_run_resumes_continuation_epic_with_matching_backend_session(isolate_tempa_paths, monkeypatch):
    tempa_config.save_config({"epic": [{
        "epic_name": "e1", "status": "pending", "completed_features": 1, "total_features": 2,
        "features": [{"id": "f1", "name": "n1", "status": "done"}, {"id": "f2", "name": "n2", "status": "pending"}],
        "session_id": "sess-123", "session_backend": "claude",
    }]})
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] == "sess-123"


def test_check_and_run_no_resume_for_brand_new_epic(isolate_tempa_paths, monkeypatch):
    # First session for this epic ever: completed_features=0, not require_fixing, no
    # session_id recorded yet — nothing to resume, same as before this change.
    tempa_config.save_config({"epic": [{
        "epic_name": "e1", "status": "pending", "completed_features": 0, "total_features": 2,
        "features": [{"id": "f1", "name": "n1", "status": "pending"}],
    }]})
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] is None


def test_check_and_run_no_resume_when_backend_mismatch(isolate_tempa_paths, monkeypatch):
    # session_id was captured under a different backend than the one now configured for
    # "implement" — get_epic_session_id refuses to hand it back (a session id from one
    # CLI is meaningless to another).
    tempa_config.save_config({
        "epic": [{
            "epic_name": "e1", "status": "pending", "completed_features": 1, "total_features": 2,
            "features": [{"id": "f1", "name": "n1", "status": "done"}, {"id": "f2", "name": "n2", "status": "pending"}],
            "session_id": "sess-123", "session_backend": "codex",
        }],
        "backends": {"implement": "claude"},
    })
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] is None


def test_check_and_run_no_resume_when_disabled_in_config(isolate_tempa_paths, monkeypatch):
    tempa_config.save_config({
        "epic": [{
            "epic_name": "e1", "status": "pending", "completed_features": 1, "total_features": 2,
            "features": [{"id": "f1", "name": "n1", "status": "done"}, {"id": "f2", "name": "n2", "status": "pending"}],
            "session_id": "sess-123", "session_backend": "claude",
        }],
        "resume_implementation_sessions": False,
    })
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] is None


def test_check_and_run_resumes_require_fixing_epic(isolate_tempa_paths, monkeypatch):
    tempa_config.save_config({"epic": [{
        "epic_name": "e1", "status": "require_fixing", "completed_features": 2, "total_features": 2,
        "features": [{"id": "f1", "name": "n1", "status": "require_fixing"}],
        "session_id": "sess-999", "session_backend": "claude",
        "qa_passed": False,
    }]})
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] == "sess-999"


def test_check_and_run_resumes_stale_on_progress_epic(isolate_tempa_paths, monkeypatch):
    # An epic left `on_progress` by a session that was interrupted (process killed,
    # machine restarted) before completing even one feature still has a resumable
    # session_id if one was ever captured — check_and_run's "always start a new session"
    # comment predates this: it now resumes that session instead of starting cold.
    tempa_config.save_config({"epic": [{
        "epic_name": "e1", "status": "on_progress", "completed_features": 0, "total_features": 3,
        "features": [{"id": "f1", "name": "n1", "status": "pending"}],
        "session_id": "sess-777", "session_backend": "claude",
    }]})
    seen = _run_check_and_run_capturing_resume(monkeypatch)
    assert seen["resume_session_id"] == "sess-777"


# ---------------------------------------------------------------------------
# _validate_and_increment_run
# ---------------------------------------------------------------------------

def test_validate_and_increment_run_increments_below_limit():
    config = {"max_session_run": 30, "epic": [{"epic_name": "e1", "status": "on_progress", "total_run": 5}]}
    result = ti._validate_and_increment_run(config, 0, "e1")
    assert result is True
    assert config["epic"][0]["total_run"] == 6
    assert config["epic"][0]["status"] == "on_progress"


def test_validate_and_increment_run_marks_epic_failed_and_persists_when_limit_reached(isolate_tempa_paths):
    # Regression: hitting the limit used to leave the epic stuck in on_progress forever with
    # no self-service recovery -- `tempa implement --reset-failed` only resets epics whose
    # status is already "failed". Marking it failed here (and persisting via save_config,
    # since the caller raises SystemExit right after without saving anything itself) is what
    # makes the next --reset-failed (or another click of Continue Implementation, which runs
    # that automatically) actually able to reset and retry it.
    config = {"max_session_run": 30, "epic": [{"epic_name": "e1", "status": "on_progress", "total_run": 30}]}
    result = ti._validate_and_increment_run(config, 0, "e1")
    assert result is False
    assert config["epic"][0]["status"] == "failed"
    assert tempa_config.load_config()["epic"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# check_and_run — QA gate feature-completeness integrity check
# ---------------------------------------------------------------------------

def test_check_and_run_qa_gate_reroutes_epic_with_incomplete_features(isolate_tempa_paths, monkeypatch):
    # Regression: a re-implementation round set the epic done without finishing each
    # feature's own bookkeeping -- QA must not run against it; it goes back to
    # require_fixing instead, and the next poll's require_fixing picker takes it from there.
    tempa_config.save_config({"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": False,
        "completed_features": 0, "total_features": 2,
        "features": [{"id": "f1", "name": "n1", "status": "require_fixing"},
                     {"id": "f2", "name": "n2", "status": "require_fixing"}],
    }]})

    def fail_if_called(*args, **kwargs):
        raise AssertionError("QA must not run when features aren't actually done")

    monkeypatch.setattr(ti, "run_qa_session", fail_if_called)

    ti.check_and_run()

    assert _state.running_thread is None
    assert tempa_config.load_config()["epic"][0]["status"] == "require_fixing"


def test_check_and_run_qa_gate_runs_qa_when_features_actually_done(isolate_tempa_paths, monkeypatch):
    tempa_config.save_config({"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": False,
        "completed_features": 2, "total_features": 2,
        "features": [{"id": "f1", "name": "n1", "status": "done"},
                     {"id": "f2", "name": "n2", "status": "done"}],
    }]})
    seen = {"called": False}

    def fake_run_qa_session(*args, **kwargs):
        seen["called"] = True

    monkeypatch.setattr(ti, "run_qa_session", fake_run_qa_session)

    ti.check_and_run()
    assert _state.running_thread is not None, "check_and_run didn't start a QA session thread"
    _state.running_thread.join(timeout=5)
    assert seen["called"] is True
    assert tempa_config.load_config()["epic"][0]["status"] == "done"


# ---------------------------------------------------------------------------
# check_and_run — self-healing a QA-passed epic whose features never got resynced
# ---------------------------------------------------------------------------

def test_check_and_run_reconciles_qa_passed_epic_with_require_fixing_features(
    isolate_tempa_paths, monkeypatch,
):
    # Regression: QA failed once (marking every feature require_fixing), the fix round set
    # the epic done without redoing the per-feature bookkeeping, and the next QA passed --
    # so the epic reads "done + QA passed" with require_fixing features forever, because
    # the QA prompt's pass branch never touches them. check_and_run repairs it in place
    # instead of scheduling any work for it.
    tempa_config.save_config({"epic": [
        {"epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done",
         "completed_features": 0, "total_features": 2,
         "features": [{"id": "f1", "name": "n1", "status": "require_fixing"},
                      {"id": "f2", "name": "n2", "status": "require_fixing"}]},
        {"epic_name": "EPIC-05", "status": "pending", "qa_passed": False,
         "completed_features": 0, "total_features": 1,
         "features": [{"id": "f1", "name": "n1", "status": "pending"}]},
    ]})

    monkeypatch.setattr(ti, "run_session", lambda *a, **k: None)
    monkeypatch.setattr(ti, "run_qa_session",
                        lambda *a, **k: pytest.fail("QA must not re-run for a passed epic"))

    ti.check_and_run()
    if _state.running_thread is not None:
        _state.running_thread.join(timeout=5)

    repaired = tempa_config.load_config()["epic"][0]
    assert [f["status"] for f in repaired["features"]] == ["done", "done"]
    assert repaired["completed_features"] == 2
    assert repaired["status"] == "done"
    assert repaired["qa_passed"] is True
