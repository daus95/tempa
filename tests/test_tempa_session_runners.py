"""Tests for tempa_session_runners.py: the per-stage session runners.

Moved here (verbatim, only the module alias changed) when the runners moved out of the
generic engine in tempa_session.py. Scope is the same carve-out as there: functions that
actually spawn a subprocess are stubbed, so what's under test is what each runner decides —
how a finished session is reported, whether a QA pass gets committed, and what the QA
round history does to an epic that keeps cycling through the gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import tempa_session_runners as tsr


@pytest.fixture(autouse=True)
def reset_runner_state():
    """_state is a module-level singleton (tempa_logging._state) shared across the whole
    process — reset the flags these tests touch before/after each test so one test's
    usage-limit/auth-error trip can't leak into the next."""
    def _clear():
        tsr._state.usage_limit_hit = False
        tsr._state.auth_error_hit = False
        tsr._state.auth_error_message = ""
        tsr._state.server_overloaded_hit = False
        tsr._state.backend_stuck_after_done_hit = False
        tsr._state.background_tasks_terminated_hit = False
        tsr._state.stop_event.clear()
    _clear()
    yield
    _clear()


# ---------------------------------------------------------------------------
# _log_session_result
# ---------------------------------------------------------------------------

def test_log_session_result_success(tmp_path):
    assert tsr._log_session_result("Session [X]", 0, tmp_path / "log.txt") is True


def test_log_session_result_usage_limit_stops_before_checking_exit_code(tmp_path):
    tsr._state.usage_limit_hit = True
    assert tsr._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


def test_log_session_result_auth_error_stops_before_checking_exit_code(tmp_path):
    tsr._state.auth_error_hit = True
    assert tsr._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


def test_log_session_result_nonzero_exit_fails(tmp_path):
    log_path = tmp_path / "log.txt"
    log_path.write_text("boom", encoding="utf-8")
    assert tsr._log_session_result("Session [X]", 1, log_path) is False


def test_log_session_result_overloaded_stops_before_checking_exit_code(tmp_path):
    tsr._state.server_overloaded_hit = True
    assert tsr._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


def test_log_session_result_background_terminated_is_not_reported_as_a_success(tmp_path, capsys):
    # The CLI exits 0 after killing its own background work, so without this the Log tab
    # showed "SUCCEEDED" for a session that was cut short mid-implementation.
    tsr._state.background_tasks_terminated_hit = True

    assert tsr._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False

    out = capsys.readouterr().out
    assert "SUCCEEDED" not in out
    assert "cut short" in out


def test_log_session_result_stuck_after_done_is_not_reported_as_a_failure(tmp_path, capsys):
    # The non-zero exit is Tempa's own force-terminate, not a task failure -- reporting it as
    # "FAILED (exit code 1)" made a routine, self-healing cleanup hang look alarming.
    tsr._state.backend_stuck_after_done_hit = True
    log_path = tmp_path / "log.txt"
    log_path.write_text("boom", encoding="utf-8")

    assert tsr._log_session_result("Session [X]", 1, log_path) is False

    out = capsys.readouterr().out
    assert "FAILED" not in out
    assert "force-terminated" in out



# ---------------------------------------------------------------------------
# run_qa_session — commit-after-QA-pass hook
#
# The backend session itself is stubbed out (real spawning is out of scope, per the
# module docstring) via `tsr._run_backend_session`; `tsr.commit_workspace_changes` is
# stubbed too, since its own real-git-repo behavior belongs to test_tempa_git.py — these
# tests only cover whether run_qa_session decides to call it, and with what. `tsr.load_config`
# is also stubbed directly (the file-wide `_no_real_wait` autouse fixture above already
# replaces it with a wait-durations-only stub, so these tests override it back to
# something with the "epic"/"workspace" shape run_qa_session actually needs) rather than
# going through a real config.json on disk.
# ---------------------------------------------------------------------------

def _stub_qa_backend_session(monkeypatch):
    monkeypatch.setattr(tsr, "_run_backend_session", lambda *a, **k: (0, Path("unused.log")))


def test_run_qa_session_commits_after_genuine_qa_pass(monkeypatch, tmp_path):
    commit = Mock(return_value=("committed", "abc123"))
    monkeypatch.setattr(tsr, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    workspace_root = str(tmp_path / "workspace")
    monkeypatch.setattr(tsr, "load_config", lambda: {
        "workspace": {"root": workspace_root},
        "epic": [{"epic_name": "EPIC-01", "status": "done", "qa_passed": True, "qa_status": "done"}],
    })

    tsr.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_called_once()
    args, _ = commit.call_args
    assert args[0] == workspace_root
    assert "EPIC-01" in args[1]


def test_run_qa_session_skips_commit_when_disabled_in_config(monkeypatch, tmp_path):
    commit = Mock()
    monkeypatch.setattr(tsr, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(tsr, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "commit_after_qa_pass": False,
        "epic": [{"epic_name": "EPIC-01", "status": "done", "qa_passed": True, "qa_status": "done"}],
    })

    tsr.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_not_called()


def test_run_qa_session_skips_commit_when_qa_did_not_actually_pass(monkeypatch, tmp_path):
    commit = Mock()
    monkeypatch.setattr(tsr, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(tsr, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "epic": [{"epic_name": "EPIC-01", "status": "require_fixing", "qa_passed": False, "qa_status": "idle"}],
    })

    tsr.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_not_called()


# ---------------------------------------------------------------------------
# _record_qa_verdict_and_guard — QA round history + loop guard
# ---------------------------------------------------------------------------

def _qa_config(epic: dict) -> dict:
    return {"epic": [epic]}


def test_record_qa_verdict_records_a_failing_round_with_the_flagged_features(tmp_path):
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False,
        "qa_status": "done", "qa_report_filename": "r1.md",
        "features": [
            {"id": "F1", "status": "require_fixing"},
            {"id": "F2", "status": "require_fixing"},
            {"id": "F3", "status": "done"},
        ],
    })

    tsr._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    history = config["epic"][0]["qa_history"]
    assert len(history) == 1
    assert history[0]["verdict"] == "fail"
    assert history[0]["failed"] == ["F1", "F2"]
    assert history[0]["report"] == "r1.md"


def test_record_qa_verdict_records_a_pass_with_no_failed_features(tmp_path):
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "done", "qa_passed": True, "qa_status": "done",
        "features": [{"id": "F1", "status": "done"}],
    })

    tsr._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert config["epic"][0]["qa_history"][0]["verdict"] == "pass"
    assert config["epic"][0]["qa_history"][0]["failed"] == []


def test_record_qa_verdict_ignores_an_interrupted_session(tmp_path):
    # qa_status is still "ongoing", so check_and_run will resume this as the SAME round —
    # recording it would count one round twice and manufacture a cycle out of an interruption.
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "done", "qa_passed": False, "qa_status": "ongoing",
    })

    tsr._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert "qa_history" not in config["epic"][0]


@pytest.mark.parametrize(
    "flag",
    ["usage_limit_hit", "auth_error_hit", "server_overloaded_hit", "backend_stuck_after_done_hit"],
)
def test_record_qa_verdict_ignores_a_session_cut_short_by_a_stop_flag(tmp_path, flag):
    setattr(tsr._state, flag, True)
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
    })

    tsr._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert "qa_history" not in config["epic"][0]


def test_record_qa_verdict_fails_the_epic_and_stops_the_runner_on_a_detected_loop(tmp_path, monkeypatch):
    notify = Mock()
    monkeypatch.setattr(tsr, "notify_attention", notify)
    epic = {
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
        "qa_loop_strikes": 1,
        "qa_history": [
            {"round": 1, "verdict": "fail", "failed": ["F1", "F2"]},
            {"round": 2, "verdict": "fail", "failed": ["F3", "F4"]},
            {"round": 3, "verdict": "fail", "failed": ["F1", "F2"]},
        ],
        "features": [
            {"id": "F3", "status": "require_fixing"},
            {"id": "F4", "status": "require_fixing"},
        ],
    }

    tsr._record_qa_verdict_and_guard(_qa_config(epic), 0, "EPIC-03", tmp_path / "qa.log")

    assert epic["status"] == "failed"
    assert "cycling through QA" in epic["blocked_reason"]
    assert tsr._state.stop_event.is_set()
    notify.assert_called_once()


def test_record_qa_verdict_leaves_a_converging_epic_alone(tmp_path):
    epic = {
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
        "qa_history": [
            {"round": 1, "verdict": "fail", "failed": ["F1", "F2", "F3"]},
            {"round": 2, "verdict": "fail", "failed": ["F1", "F2"]},
        ],
        "features": [{"id": "F1", "status": "require_fixing"}],
    }

    tsr._record_qa_verdict_and_guard(_qa_config(epic), 0, "EPIC-03", tmp_path / "qa.log")

    assert epic["status"] == "require_fixing"
    assert "blocked_reason" not in epic
    assert not tsr._state.stop_event.is_set()


def test_run_qa_session_records_the_round_it_just_finished(monkeypatch, tmp_path):
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(tsr, "commit_workspace_changes", Mock())
    monkeypatch.setattr(tsr, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "epic": [{
            "epic_name": "EPIC-01", "status": "require_fixing", "qa_passed": False,
            "qa_status": "done", "features": [{"id": "F1", "status": "require_fixing"}],
        }],
    })
    saved = {}
    monkeypatch.setattr(tsr, "save_config", lambda config: saved.update(config))

    tsr.run_qa_session(0, "prompt", "EPIC-01")

    assert saved["epic"][0]["qa_history"][0]["failed"] == ["F1"]
