"""Tests for tempa_session_outcome.py: what a finished implementation session means for
its epic.

Two layers are covered. The decision helpers — no-progress tracking, the
last-meaningful-log-lines explanation surfaced to the user, the automatic dependency
reorder, telling a genuinely-complete epic apart from a blocked one, and the QA-state
desync repair — moved here verbatim from test_tempa_session.py when the decision tree moved
out of the session engine. On top of those, apply_session_outcome itself: it spawns nothing
(run_session already did that and hands it only the result), so every branch of the tree it
walks is reachable here by setting _state's flags and an exit code.
"""

from __future__ import annotations

import pytest

import tempa_config
import tempa_session_outcome as tso
from tempa_backend import get_backend_def


@pytest.fixture(autouse=True)
def reset_runner_state():
    """_state is a process-wide singleton (tempa_logging._state) — clear the stop flags
    each test sets so one test's usage-limit/auth trip can't leak into the next."""
    def _clear():
        tso._state.usage_limit_hit = False
        tso._state.auth_error_hit = False
        tso._state.server_overloaded_hit = False
        tso._state.backend_stuck_after_done_hit = False
        tso._state.background_tasks_terminated_hit = False
        tso._state.stop_event.clear()
    _clear()
    yield
    _clear()

# ---------------------------------------------------------------------------
# _update_no_progress_tracking
# ---------------------------------------------------------------------------

def test_update_no_progress_tracking_resets_counter_on_progress():
    epic = {"completed_features": 3, "no_progress_rounds": 1}
    stalled = tso._update_no_progress_tracking(epic, completed_before=2, limit=2)
    assert stalled is False
    assert epic["no_progress_rounds"] == 0


def test_update_no_progress_tracking_also_resets_total_run_on_progress():
    # Regression: a long-running epic that legitimately needs many resumes across its
    # natural lifecycle (many features_per_session batches, several QA fix-rounds) must not
    # keep accumulating toward max_session_run for reasons unrelated to being stuck — every
    # real forward-progress round resets that lifetime counter too, not just no_progress_rounds.
    epic = {"completed_features": 3, "no_progress_rounds": 1, "total_run": 29}
    tso._update_no_progress_tracking(epic, completed_before=2, limit=2)
    assert epic["total_run"] == 0


def test_update_no_progress_tracking_leaves_total_run_untouched_when_stalled():
    epic = {"completed_features": 1, "no_progress_rounds": 0, "total_run": 29}
    tso._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert epic["total_run"] == 29


def test_update_no_progress_tracking_increments_counter_when_stalled():
    epic = {"completed_features": 1, "no_progress_rounds": 0}
    stalled = tso._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert stalled is False
    assert epic["no_progress_rounds"] == 1


def test_update_no_progress_tracking_reaches_limit():
    epic = {"completed_features": 1, "no_progress_rounds": 1}
    stalled = tso._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert stalled is True
    assert epic["no_progress_rounds"] == 2


def test_update_no_progress_tracking_starts_from_zero_when_absent():
    epic = {"completed_features": 1}
    stalled = tso._update_no_progress_tracking(epic, completed_before=1, limit=1)
    assert stalled is True
    assert epic["no_progress_rounds"] == 1


# ---------------------------------------------------------------------------
# _last_meaningful_log_lines
# ---------------------------------------------------------------------------

def test_last_meaningful_log_lines_drops_done_accounting_line(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("line one\nline two\n[Done] input=123 output=45\n", encoding="utf-8")
    assert tso._last_meaningful_log_lines(log_path) == "line one\nline two"


def test_last_meaningful_log_lines_skips_blank_lines(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("line one\n\n\nline two\n", encoding="utf-8")
    assert tso._last_meaningful_log_lines(log_path) == "line one\nline two"


def test_last_meaningful_log_lines_caps_to_max_lines(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")
    result = tso._last_meaningful_log_lines(log_path, max_lines=3)
    assert result == "line 7\nline 8\nline 9"


def test_last_meaningful_log_lines_missing_file_returns_empty(tmp_path):
    assert tso._last_meaningful_log_lines(tmp_path / "missing.txt") == ""


# ---------------------------------------------------------------------------
# _try_reorder_for_dependency
# ---------------------------------------------------------------------------

def _epics(*names_and_statuses):
    return [{"epic_name": n, "status": s} for n, s in names_and_statuses]


def test_try_reorder_for_dependency_moves_target_before_stuck_epic():
    config = {"epic": _epics(("EPIC-16", "on_progress"), ("EPIC-17", "pending"), ("EPIC-18", "pending"))}
    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-17")
    assert result is None
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-17", "EPIC-16", "EPIC-18"]
    assert config["epic_reorder_history"] == [["EPIC-17", "EPIC-16"]]


def test_try_reorder_for_dependency_refuses_self_reference():
    config = {"epic": _epics(("EPIC-16", "on_progress"))}
    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-16")
    assert result is not None and "can't be blocked on itself" in result
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-16"]
    assert "epic_reorder_history" not in config


def test_try_reorder_for_dependency_refuses_unknown_epic():
    config = {"epic": _epics(("EPIC-16", "on_progress"))}
    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-99")
    assert result is not None and "not a known epic" in result
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-16"]


def test_try_reorder_for_dependency_refuses_already_done_target():
    config = {"epic": _epics(("EPIC-16", "on_progress"), ("EPIC-17", "done"))}
    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-17")
    assert result is not None and "already done" in result


def test_try_reorder_for_dependency_refuses_target_already_before_stuck_epic():
    config = {"epic": _epics(("EPIC-17", "pending"), ("EPIC-16", "on_progress"))}
    result = tso._try_reorder_for_dependency(config, stuck_index=1, blocked_by_epic="EPIC-17")
    assert result is not None and "already scheduled before" in result


def test_try_reorder_for_dependency_refuses_circular_reversal():
    # EPIC-17 was already moved ahead of EPIC-16 once (history reflects that). Now EPIC-17
    # itself stalls claiming the reverse ("blocked on EPIC-16") — moving EPIC-16 back ahead
    # of EPIC-17 would just undo the first move, a likely circular dependency, so refuse.
    config = {
        "epic": _epics(("EPIC-17", "pending"), ("EPIC-16", "on_progress")),
        "epic_reorder_history": [["EPIC-17", "EPIC-16"]],
    }
    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-16")
    assert result is not None and "circular dependency" in result


# ---------------------------------------------------------------------------
# _epic_genuinely_complete / _repair_qa_state_desync
# ---------------------------------------------------------------------------

def test_epic_genuinely_complete_true_when_all_features_done():
    epic = {
        "total_features": 2, "completed_features": 2,
        "features": [{"status": "done"}, {"status": "done"}],
    }
    assert tso._epic_genuinely_complete(epic) is True


def test_epic_genuinely_complete_false_when_features_incomplete():
    epic = {
        "total_features": 2, "completed_features": 1,
        "features": [{"status": "done"}, {"status": "pending"}],
    }
    assert tso._epic_genuinely_complete(epic) is False


def test_epic_genuinely_complete_false_when_total_features_zero():
    # No features ever recorded — _epic_features_actually_done alone would vacuously
    # return True here; the total>0 guard is what prevents a false "genuinely complete".
    epic = {"total_features": 0, "completed_features": 0, "features": []}
    assert tso._epic_genuinely_complete(epic) is False


def test_epic_genuinely_complete_false_when_feature_status_disagrees():
    # completed_features/total_features agree at the epic level, but a feature's own
    # status still says otherwise — the underlying integrity check must still catch this.
    epic = {
        "total_features": 2, "completed_features": 2,
        "features": [{"status": "done"}, {"status": "require_fixing"}],
    }
    assert tso._epic_genuinely_complete(epic) is False


def test_repair_qa_state_desync_routes_back_through_qa_gate():
    epic = {
        "status": "require_fixing", "qa_passed": False, "qa_status": "idle",
        "no_progress_rounds": 2, "total_features": 3, "completed_features": 3,
    }
    tso._repair_qa_state_desync(epic)
    assert epic["status"] == "done"
    assert epic["qa_passed"] is False
    assert epic["qa_status"] == "idle"
    assert epic["no_progress_rounds"] == 0


# ---------------------------------------------------------------------------
# _reason_with_counterpart_context
# ---------------------------------------------------------------------------

def test_reason_with_counterpart_context_appends_when_counterpart_has_a_reason():
    epics = _epics(("EPIC-16", "failed"), ("EPIC-17", "failed"))
    epics[1]["blocked_reason"] = "needs something from EPIC-16"
    result = tso._reason_with_counterpart_context("needs something from EPIC-17", epics, "EPIC-17")
    assert result == (
        "needs something from EPIC-17\n\n"
        "For context, 'EPIC-17' itself previously reported being blocked:\n"
        "needs something from EPIC-16"
    )


def test_reason_with_counterpart_context_unchanged_when_counterpart_has_no_reason():
    epics = _epics(("EPIC-16", "failed"), ("EPIC-17", "pending"))
    result = tso._reason_with_counterpart_context("needs something from EPIC-17", epics, "EPIC-17")
    assert result == "needs something from EPIC-17"


def test_reason_with_counterpart_context_unchanged_when_no_target_named():
    epics = _epics(("EPIC-16", "failed"))
    result = tso._reason_with_counterpart_context("some reason", epics, None)
    assert result == "some reason"




# ---------------------------------------------------------------------------
# apply_session_outcome — the decision tree run_session hands its result to
# ---------------------------------------------------------------------------
BACKEND = get_backend_def("claude")


def _write_config(epics, **extra):
    config = tempa_config.load_config()
    config["epic"] = epics
    config.update(extra)
    tempa_config.save_config(config)
    return config


def _epic(name="EPIC-01", **overrides):
    epic = {"epic_name": name, "status": "on_progress", "completed_features": 1,
            "total_features": 3, "features": [], "no_progress_rounds": 0}
    epic.update(overrides)
    return epic


def _apply(index=0, label="EPIC-01", exit_code=0, log_path=None, completed_before=1, tmp_path=None):
    tso.apply_session_outcome(index, label, exit_code,
                              log_path or (tmp_path / "session.txt"), completed_before, BACKEND)


def _saved_epic(index=0):
    return tempa_config.load_config()["epic"][index]


def test_a_nonzero_exit_marks_the_epic_failed_and_stops_the_run(tmp_path):
    _write_config([_epic()])
    _apply(exit_code=1, tmp_path=tmp_path)
    assert _saved_epic()["status"] == "failed"
    assert tso._state.stop_event.is_set()


@pytest.mark.parametrize("flag", [
    "usage_limit_hit", "auth_error_hit", "server_overloaded_hit", "backend_stuck_after_done_hit",
])
def test_a_pause_condition_leaves_the_epic_resumable(tmp_path, flag):
    """A usage limit, auth error, overload, or forced post-[Done] termination is a pause,
    not a failure — the epic keeps its status so the next poll resumes it."""
    _write_config([_epic()])
    setattr(tso._state, flag, True)
    _apply(exit_code=1, tmp_path=tmp_path)
    assert _saved_epic()["status"] == "on_progress"
    assert not tso._state.stop_event.is_set()


def test_a_session_cut_short_by_the_backend_is_not_counted_as_no_progress(tmp_path):
    _write_config([_epic(no_progress_rounds=0)])
    tso._state.background_tasks_terminated_hit = True
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)
    saved = _saved_epic()
    assert saved["status"] == "on_progress"
    assert saved["no_progress_rounds"] == 0


def test_real_progress_clears_the_no_progress_counter(tmp_path):
    _write_config([_epic(completed_features=2, no_progress_rounds=1)])
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)
    saved = _saved_epic()
    assert saved["no_progress_rounds"] == 0
    assert saved["status"] == "on_progress"


def test_a_stalled_round_below_the_limit_only_increments_the_counter(tmp_path):
    _write_config([_epic(no_progress_rounds=0)], implement_no_progress_rounds=2)
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)
    saved = _saved_epic()
    assert saved["no_progress_rounds"] == 1
    assert saved["status"] == "on_progress"
    assert "blocked_reason" not in saved


def test_hitting_the_limit_with_a_named_blocker_reorders_the_plan(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("blocked on EPIC-02's API\n", encoding="utf-8")
    _write_config(
        [_epic("EPIC-01", no_progress_rounds=1, blocked_by_epic="EPIC-02"),
         _epic("EPIC-02", status="pending")],
        implement_no_progress_rounds=2,
    )
    _apply(exit_code=0, completed_before=1, log_path=log_path)
    config = tempa_config.load_config()
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-02", "EPIC-01"]
    stuck = next(e for e in config["epic"] if e["epic_name"] == "EPIC-01")
    assert stuck["status"] == "pending"
    assert stuck["no_progress_rounds"] == 0
    assert stuck["blocked_reason"] == "blocked on EPIC-02's API"
    assert not tso._state.stop_event.is_set()


def test_hitting_the_limit_on_an_actually_complete_epic_routes_it_back_through_qa(tmp_path):
    _write_config(
        [_epic(completed_features=2, total_features=2, no_progress_rounds=1,
               features=[{"id": "F1", "status": "done"}, {"id": "F2", "status": "done"}])],
        implement_no_progress_rounds=2,
    )
    _apply(exit_code=0, completed_before=2, tmp_path=tmp_path)
    saved = _saved_epic()
    assert (saved["status"], saved["qa_passed"], saved["qa_status"]) == ("done", False, "idle")
    assert saved["no_progress_rounds"] == 0
    assert not tso._state.stop_event.is_set()


def test_hitting_the_limit_with_no_way_forward_fails_the_epic(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("waiting on something external\n", encoding="utf-8")
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _apply(exit_code=0, completed_before=1, log_path=log_path)
    saved = _saved_epic()
    assert saved["status"] == "failed"
    assert saved["blocked_reason"] == "waiting on something external"
    assert tso._state.stop_event.is_set()


def test_a_done_epic_is_left_alone(tmp_path):
    """Only on_progress/require_fixing epics go through the no-progress guard — an epic the
    session itself marked done is on its way to the QA gate and must not be touched here."""
    _write_config([_epic(status="done", no_progress_rounds=0)], implement_no_progress_rounds=1)
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)
    saved = _saved_epic()
    assert saved["status"] == "done"
    assert saved["no_progress_rounds"] == 0
