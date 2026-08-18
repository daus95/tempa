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
        tso._state.reclaimed_process_count = 0
        tso._state.last_agent_message = ""
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


def test_a_nonzero_exit_says_why_and_how_to_retry_on_the_epic(tmp_path):
    """This path used to set the status alone. The dashboard's Halted panel renders
    blocked_reason and nothing else, so the epic showed up as a bare red ✗ with the exit code
    and the log name buried in the process log."""
    _write_config([_epic()])
    _apply(exit_code=1, tmp_path=tmp_path)

    reason = _saved_epic()["blocked_reason"]
    assert "exited with code 1" in reason
    assert "session.txt" in reason
    assert "Continue Implementation" in reason


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
    assert saved["blocked_reason"].startswith("waiting on something external")
    # blocked_reason is the whole of what the dashboard's Halted panel shows, so it has to
    # carry the way out too — not just the diagnosis.
    assert "Continue Implementation" in saved["blocked_reason"]
    assert tso._state.stop_event.is_set()


def test_a_done_epic_is_left_alone(tmp_path):
    """Only on_progress/require_fixing epics go through the no-progress guard — an epic the
    session itself marked done is on its way to the QA gate and must not be touched here."""
    _write_config([_epic(status="done", no_progress_rounds=0)], implement_no_progress_rounds=1)
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)
    saved = _saved_epic()
    assert saved["status"] == "done"
    assert saved["no_progress_rounds"] == 0


# ---------------------------------------------------------------------------
# Deferral — a feature that needs a decision only the user can make
# ---------------------------------------------------------------------------

def _blocked_feature(fid="F2", **overrides):
    feature = {"id": fid, "name": "Workflow engine migration", "status": "blocked",
               "blocked_question": "Migrate onto the shared engine, or descope it?",
               "blocked_recommendation": "Descope — the merge semantics need fixing first.",
               "blocked_answer": ""}
    feature.update(overrides)
    return feature


def test_a_blocked_feature_that_is_all_thats_left_defers_instead_of_failing(tmp_path):
    """The case this whole path exists for: the session did its job and the honest answer is
    "someone has to choose". Failing that is what stopped a whole run overnight."""
    _write_config(
        [_epic(completed_features=1, total_features=2,
               features=[{"id": "F1", "status": "done"}, _blocked_feature()])],
        implement_no_progress_rounds=2,
    )
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    saved = _saved_epic()
    assert saved["status"] == "deferred"
    assert "Migrate onto the shared engine" in saved["blocked_reason"]
    # The recommendation travels with the question — "yes, do that" is the common answer, and
    # having to open a log to find out what was suggested is what defers a decision for days.
    assert "Descope" in saved["blocked_reason"]
    assert "blocked_answer" in saved["blocked_reason"]


def test_deferring_never_stops_the_runner(tmp_path):
    """The point of deferring rather than failing: later epics that have nothing to do with the
    question keep getting built while it waits."""
    _write_config(
        [_epic(completed_features=1, total_features=2,
               features=[{"id": "F1", "status": "done"}, _blocked_feature()])],
    )
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    assert not tso._state.stop_event.is_set()


def test_deferral_does_not_wait_for_the_stall_limit(tmp_path):
    """Resuming twice more to re-confirm a question already sitting with the user is pure waste
    — and each of those rounds completes no feature, so it would end in `failed`, not `deferred`."""
    _write_config(
        [_epic(completed_features=1, total_features=2, no_progress_rounds=0,
               features=[{"id": "F1", "status": "done"}, _blocked_feature()])],
        implement_no_progress_rounds=3,
    )
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    assert _saved_epic()["status"] == "deferred"


def test_an_epic_with_other_work_left_is_not_deferred_by_one_blocked_feature(tmp_path):
    """Features 3+ don't stop being buildable because feature 2 needs a decision — and the stall
    counter keeps running, so this can't be used to park an epic that still has work in it."""
    _write_config(
        [_epic(completed_features=1, total_features=3, no_progress_rounds=0,
               features=[{"id": "F1", "status": "done"}, _blocked_feature(),
                         {"id": "F3", "status": "pending"}])],
        implement_no_progress_rounds=2,
    )
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    saved = _saved_epic()
    assert saved["status"] == "on_progress"
    assert saved["no_progress_rounds"] == 1


def test_a_blocked_feature_that_has_been_answered_is_not_still_waiting(tmp_path):
    """An answered feature is work, not a question — it must not re-defer the epic on the round
    that was about to act on it."""
    _write_config(
        [_epic(completed_features=1, total_features=2, no_progress_rounds=1,
               features=[{"id": "F1", "status": "done"},
                         _blocked_feature(blocked_answer="Descope it, per your recommendation.")])],
        implement_no_progress_rounds=2,
    )
    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    assert _saved_epic()["status"] != "deferred"


def test_reorder_catches_a_cycle_the_counterpart_already_declared(tmp_path):
    """The cycle stated outright in config.json, before any reorder has been attempted: EPIC-02
    records that it's blocked on EPIC-04 while EPIC-04 stalls claiming EPIC-02. Inferring this
    from epic_reorder_history instead costs a round and a pointless reorder first."""
    config = {"epic": [
        {"epic_name": "EPIC-04", "status": "on_progress"},
        {"epic_name": "EPIC-02", "status": "pending", "blocked_by_epic": "EPIC-04"},
    ]}

    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-02")

    assert result is not None and "circular dependency" in result
    # Nothing was moved — a cycle has no safe ordering to move it into.
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-04", "EPIC-02"]


def test_a_cycle_refusal_says_what_a_human_can_actually_change(tmp_path):
    """No reordering satisfies "A before B" and "B before A", so this branch's answer is always a
    plan change — and the options are short and knowable, so they belong in the message that
    stops the run rather than in two epic specs and a log."""
    config = {"epic": [
        {"epic_name": "EPIC-04", "status": "on_progress"},
        {"epic_name": "EPIC-02", "status": "pending", "blocked_by_epic": "EPIC-04"},
    ]}

    result = tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-02")

    assert "merge them into a single epic" in result
    assert "permanent implementation" in result
    assert "blocked_by_epic" in result


def test_an_ordinary_out_of_order_dependency_still_reorders(tmp_path):
    """The cycle checks must not swallow the common case they sit in front of."""
    config = {"epic": _epics(("EPIC-04", "on_progress"), ("EPIC-17", "pending"))}

    assert tso._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-17") is None
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-17", "EPIC-04"]


# ---------------------------------------------------------------------------
# last_round_note — carrying a stalled round's own conclusion to the next one
# ---------------------------------------------------------------------------

def test_a_stalled_round_records_its_own_conclusion_before_the_limit(tmp_path):
    """Recorded on EVERY stalled round, not only the one that trips the limit — otherwise the
    round that actually worked the blocker out leaves nothing behind for the next one."""
    tso._state.last_agent_message = "m11.workflow.levels has no per-key merge."
    _write_config([_epic(no_progress_rounds=0)], implement_no_progress_rounds=3)

    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    saved = _saved_epic()
    assert saved["last_round_note"] == "m11.workflow.levels has no per-key merge."
    assert saved["status"] == "on_progress"          # not at the limit yet


def test_a_round_that_makes_progress_clears_the_stale_note(tmp_path):
    """It describes a state this round just moved past — carrying it on would argue against work
    that has already happened."""
    tso._state.last_agent_message = "all good"
    _write_config([_epic(completed_features=2, last_round_note="blocked on X")])

    _apply(exit_code=0, completed_before=1, tmp_path=tmp_path)

    assert "last_round_note" not in _saved_epic()


def test_the_halt_reason_quotes_what_the_agent_said_not_what_its_tools_printed(tmp_path):
    """blocked_reason is the whole of what the dashboard's Halted panel shows. Tailing the log
    for it put a psql table header and `(0 rows)` in one epic's reason, and an Edit tool's
    success message in another's — a tool result is logged as one chunk whose content spans
    lines that carry no marker, so the log tail cannot be separated back out."""
    log_path = tmp_path / "session.txt"
    log_path.write_text(
        "[Result]  table_name \n------------\n(0 rows)\n"
        "Nothing has changed since last session.\n[Done] turns=3\n",
        encoding="utf-8",
    )
    tso._state.last_agent_message = "Nothing has changed since last session."
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)

    _apply(exit_code=0, completed_before=1, log_path=log_path)

    reason = _saved_epic()["blocked_reason"]
    assert reason.startswith("Nothing has changed since last session.")
    assert "(0 rows)" not in reason


def test_the_halt_reason_falls_back_to_the_log_when_nothing_was_captured(tmp_path):
    """A session that produced no prose at all still beats an empty Halted panel."""
    log_path = tmp_path / "session.txt"
    log_path.write_text("some closing words\n[Done] turns=1\n", encoding="utf-8")
    tso._state.last_agent_message = ""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)

    _apply(exit_code=0, completed_before=1, log_path=log_path)

    assert "some closing words" in _saved_epic()["blocked_reason"]


# ---------------------------------------------------------------------------
# _ended_waiting_on_killed_work / the cut-short grace
#
# The 2026-08-18 EPIC-02 incident: a session did 182 turns of QA fixes, live-verified three
# features, started a 12-minute `dotnet test` in the background and closed its turn saying it
# would report back once it finished. That reply ended the run; Tempa's container teardown then
# reclaimed 38 processes including the test. Two such rounds is what implement_no_progress_rounds
# reads as an epic blocked on something outside itself, so it was failed and the runner stopped.
# ---------------------------------------------------------------------------

# The incident's own closing sentence, verbatim from session_EPIC-02_20260818_162124.txt.
_INCIDENT_CLOSE = (
    "I'm waiting for the full failure-list test run (background) to complete so I can confirm "
    "whether the 16th failure is a genuine regression or matches the pre-existing baseline. "
    "I'll report back once it finishes."
)


def _cut_short(message=_INCIDENT_CLOSE, reclaimed=38):
    """Put _state into the shape a round Tempa cut short leaves behind."""
    tso._state.last_agent_message = message
    tso._state.reclaimed_process_count = reclaimed


def test_a_round_tempa_cut_short_does_not_trip_the_stall_limit(tmp_path):
    """The load-bearing half is that the round is STILL COUNTED: no_progress_rounds reaches the
    limit honestly, and only the verdict is deferred. A design that stopped counting would be an
    exemption, and 82% of exit-0 sessions in the incident workspace reclaimed something."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    epic = _saved_epic()
    assert epic["status"] == "on_progress"
    assert epic["no_progress_rounds"] == 2
    assert epic["cut_short_rounds"] == 1
    assert not tso._state.stop_event.is_set()


def test_the_cut_short_grace_runs_out_and_the_epic_still_fails(tmp_path):
    """The design's falsifier: this test fails under a blanket exemption."""
    _write_config([_epic(no_progress_rounds=1, cut_short_rounds=1)], implement_no_progress_rounds=2)
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["status"] == "failed"
    assert tso._state.stop_event.is_set()


def test_a_reclaim_without_a_promise_to_come_back_is_counted_as_before(tmp_path):
    """Reclaim alone grants nothing - it is the 82% signal, not evidence."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short(message="The schema owner has not added the column.", reclaimed=40)
    _apply(tmp_path=tmp_path, completed_before=1)

    epic = _saved_epic()
    assert epic["status"] == "failed"
    assert "cut_short_rounds" not in epic


def test_a_promise_to_come_back_with_nothing_reclaimed_is_counted_as_before(tmp_path):
    """Corroboration is required, not optional: prose alone cannot carry the classification."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short(reclaimed=0)
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["status"] == "failed"


def test_a_promise_found_only_above_the_closing_paragraph_is_not_a_waiting_close(tmp_path):
    """Only the LAST paragraph counts. A session that mentioned reporting back earlier and then
    closed with a summary of what it changed did not end its turn to wait."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short(message=(
        "Kicked off the suite and I'll report back once it finishes.\n\n"
        "## What changed\n- ParameterValueWriteService now publishes the change event\n"
        "- ReadinessCheckResult gained a status field\n- Added the pending-check spec"
    ))
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["status"] == "failed"


def test_a_stuck_after_done_round_never_earns_the_grace(tmp_path):
    """Checked as a flag rather than inferred from the count: terminate_tree() counts first and
    leaves the job handle set, so a later close() can re-count from another thread."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short()
    tso._state.backend_stuck_after_done_hit = True
    tso._handle_stalled_round(tempa_config.load_config(), 0, "EPIC-01", 1, tmp_path / "s.txt")

    assert "cut_short_rounds" not in _saved_epic()


def test_the_grace_never_delays_an_automatic_reorder(tmp_path):
    """Both automatic repairs need no human and must keep firing on the round they always have.
    Only the verdict that costs a human is deferred."""
    _write_config(
        [_epic(no_progress_rounds=1, blocked_by_epic="EPIC-17"),
         {"epic_name": "EPIC-17", "status": "pending"}],
        implement_no_progress_rounds=2,
    )
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    config = tempa_config.load_config()
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-17", "EPIC-01"]
    assert config["epic"][1]["status"] == "pending"
    assert "cut_short_rounds" not in config["epic"][1]


def test_the_grace_never_delays_a_qa_state_desync_repair(tmp_path):
    """Same reason as the reorder: this repair is the automatic fix for the config write race
    save_config's own docstring documents."""
    _write_config([_epic(
        no_progress_rounds=1, completed_features=2, total_features=2,
        features=[{"id": "F1", "status": "done"}, {"id": "F2", "status": "done"}],
    )], implement_no_progress_rounds=2)
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=2)

    epic = _saved_epic()
    assert epic["status"] == "done"
    assert epic["qa_status"] == "idle"
    assert "cut_short_rounds" not in epic


def test_progress_clears_the_grace_an_interrupted_round_earned(tmp_path):
    """Grace is for an epic that was interrupted, not one that is stuck."""
    _write_config([_epic(
        no_progress_rounds=1, cut_short_rounds=1, completed_features=2,
        last_round_note="something", last_round_note_kind="unfinished_check",
    )], implement_no_progress_rounds=2)
    _apply(tmp_path=tmp_path, completed_before=1)

    epic = _saved_epic()
    assert epic["no_progress_rounds"] == 0
    assert "cut_short_rounds" not in epic
    assert "last_round_note_kind" not in epic


def test_a_waiting_close_is_recorded_as_what_it_is_not_as_a_blocker(tmp_path):
    """The Halted panel renders blocked_reason and nothing else. On EPIC-02 a human opened it and
    read a sentence about waiting for a test run, which names no blocker and reads as though the
    run were still going."""
    _write_config([_epic(no_progress_rounds=1, cut_short_rounds=1)], implement_no_progress_rounds=2)
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    reason = _saved_epic()["blocked_reason"]
    assert reason.startswith("This round did not report a blocker")
    # The session's own words are quoted underneath, never suppressed: on the real incident the
    # closing message was the only record anywhere that three features had been finished.
    assert _INCIDENT_CLOSE in reason
    assert "Continue Implementation" in reason


def test_a_blocker_that_says_waiting_is_left_exactly_as_the_session_wrote_it(tmp_path):
    """The named hazard, pinned: classifying by topic rather than tense would drop a real
    blocker's own words from the panel."""
    blocker = "Blocked waiting on the client's API key - nothing here can proceed without it."
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short(message=blocker, reclaimed=40)
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["blocked_reason"].startswith(blocker)


def test_a_second_waiting_halt_stops_reassuring_the_reader(tmp_path):
    """Continue Implementation runs --reset-failed itself, which zeroes every counter - so a
    panel that always said "no investigation needed" would rubber-stamp an unbounded loop."""
    _write_config(
        [_epic(no_progress_rounds=1, cut_short_rounds=1, ended_waiting_halts=1)],
        implement_no_progress_rounds=2,
    )
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    reason = _saved_epic()["blocked_reason"]
    assert "2th time" in reason
    assert "completed_features" in reason


def test_a_halt_with_no_named_blocker_does_not_claim_an_outside_blocker(tmp_path):
    """The old message asserted "very likely blocked on something outside this epic" and then
    conceded, one line later, that no epic had been named. The claim is the part a human reads
    first, and it sends them hunting a dependency that does not exist."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    tso._state.last_agent_message = "Ran out of turns partway through the migration."
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["status"] == "failed"


def test_a_stalled_round_with_nothing_to_quote_still_explains_itself(tmp_path):
    """blocked_reason is assigned unconditionally on this path, so a round with no prose and an
    unreadable log stored "". The dashboard renders its Halted panel only when that field is
    truthy, so the epic became a bare red cross with no way out anywhere on the page."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _apply(tmp_path=tmp_path, completed_before=1, log_path=tmp_path / "does-not-exist.txt")

    reason = _saved_epic()["blocked_reason"]
    assert reason
    assert "does-not-exist.txt" in reason
    assert "--reset-failed" in reason


def test_an_unfinished_check_is_labelled_as_one_for_the_next_round(tmp_path):
    _write_config([_epic(no_progress_rounds=0)], implement_no_progress_rounds=3)
    _cut_short()
    _apply(tmp_path=tmp_path, completed_before=1)

    epic = _saved_epic()
    assert epic["last_round_note"] == _INCIDENT_CLOSE
    assert epic["last_round_note_kind"] == "unfinished_check"


def test_a_note_that_is_a_real_conclusion_carries_no_kind(tmp_path):
    _write_config([_epic(no_progress_rounds=0)], implement_no_progress_rounds=3)
    _cut_short(message="The shared engine's merge semantics are wrong.", reclaimed=0)
    _apply(tmp_path=tmp_path, completed_before=1)

    epic = _saved_epic()
    assert epic["last_round_note"]
    assert "last_round_note_kind" not in epic


def test_a_non_english_waiting_close_falls_back_to_todays_behaviour(tmp_path):
    """Pinning the limitation rather than leaving it to be discovered: the phrase list is English
    and Claude-shaped, and a miss degrades to exactly the behaviour Tempa had before."""
    _write_config([_epic(no_progress_rounds=1)], implement_no_progress_rounds=2)
    _cut_short(message="Menunggu test suite selesai - akan saya laporkan setelah selesai.")
    _apply(tmp_path=tmp_path, completed_before=1)

    assert _saved_epic()["status"] == "failed"


def test_tempas_own_reclaim_line_is_not_quoted_back_as_the_sessions_explanation(tmp_path):
    """_log_reclaimed appends its message into the session log AFTER the agent's last word, so on
    any session that left processes running it IS the tail. The [Done] trim cannot catch it: that
    only fires when [Done] is the last line, and the reclaim paragraph lands after it."""
    log_path = tmp_path / "session.txt"
    log_path.write_text(
        "I've completed the code changes and live verification for all three features.\n"
        "[Done] turns=182 cost=$?\n"
        "\n"
        "[EPIC-02] the backend CLI exited but left 38 process(es) of its own still running - "
        "typically a dev server, build daemon, watcher or test runner it started and never "
        "stopped. Turn off Terminate leftover processes in Settings.\n",
        encoding="utf-8",
    )
    tso._state.last_agent_message = ""

    reason = tso._last_meaningful_log_lines(log_path)
    assert reason == "I've completed the code changes and live verification for all three features."


def test_a_reclaim_line_quoted_inside_a_tool_result_does_not_truncate_the_log(tmp_path):
    """A session that cats a Tempa log mid-round must not be able to truncate its own explanation
    at the quoted copy - hence the near-start-of-line bound.

    The bound is a bound, not a proof: a quoted copy landing within the first 80 characters of a
    line still trims, which is the deliberately accepted residue (it costs a fallback reason, not
    a real one, since the capture in _remember_agent_message is preferred whenever it exists).
    This fixture is what the real shape looks like - a [Result] line carrying a command and its
    output before the quoted text begins."""
    log_path = tmp_path / "session.txt"
    log_path.write_text(
        "[Result] $ tail -3 /c/work/repo/other-workspace/.tempa/logs/process_20260818_150522.txt "
        "which printed the following three lines from that other run: "
        "] the backend CLI exited but left 3 process(es) of its own still running\n"
        "The migration is done and the suite is green.\n",
        encoding="utf-8",
    )
    tso._state.last_agent_message = ""

    assert tso._last_meaningful_log_lines(log_path).endswith("the suite is green.")
