"""Tests for tempa_maintenance.py — the destructive `clear`/reset commands: the safety
check that guards every delete target, the deterministic file operations themselves, and
the confirmation prompt. All file I/O goes through tmp_path; nothing here touches a real
subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import tempa_config
import tempa_maintenance as tm

# ---------------------------------------------------------------------------
# _safety_check_clear_target
# ---------------------------------------------------------------------------

def test_safety_check_rejects_drive_root(tmp_path):
    drive_root = Path(tmp_path.anchor)
    with pytest.raises(SystemExit) as exc:
        tm._safety_check_clear_target(drive_root, str(tmp_path))
    assert exc.value.code == 1


def test_safety_check_rejects_outside_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    with pytest.raises(SystemExit) as exc:
        tm._safety_check_clear_target(outside, str(root))
    assert exc.value.code == 1


def test_safety_check_allows_target_equal_to_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    tm._safety_check_clear_target(root, str(root))  # should not raise


def test_safety_check_allows_descendant_of_root(tmp_path):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    tm._safety_check_clear_target(child, str(root))  # should not raise


def test_safety_check_empty_root_is_permissive(tmp_path):
    somewhere = tmp_path / "somewhere"
    somewhere.mkdir()
    tm._safety_check_clear_target(somewhere, "")  # should not raise


def test_safety_check_relative_path_resolved_via_cwd(tmp_path, monkeypatch):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    monkeypatch.chdir(root)
    tm._safety_check_clear_target(Path("child"), str(root))  # should not raise


# ---------------------------------------------------------------------------
# _do_clear_implement / _do_clear_plan / _do_clear_clarify
# ---------------------------------------------------------------------------

def test_do_clear_implement_dirs_absent(tmp_path, isolate_tempa_paths):
    assert tm._do_clear_implement() == (0, 0)


def test_do_clear_implement_deletes_files_and_nested_dirs(isolate_tempa_paths):
    qa_dir = tempa_config.get_qa_dir()
    logs_dir = tempa_config.get_logs_dir()
    (qa_dir / "sub").mkdir(parents=True)
    (qa_dir / "sub" / "nested.txt").write_text("x", encoding="utf-8")
    (qa_dir / "top.txt").write_text("x", encoding="utf-8")
    logs_dir.mkdir(parents=True)
    (logs_dir / "log1.txt").write_text("x", encoding="utf-8")

    qa_count, logs_count = tm._do_clear_implement()

    assert qa_count == 2
    assert logs_count == 1
    assert list(qa_dir.iterdir()) == []
    assert list(logs_dir.iterdir()) == []


def test_do_clear_implement_only_logs_populated(isolate_tempa_paths):
    logs_dir = tempa_config.get_logs_dir()
    logs_dir.mkdir(parents=True)
    (logs_dir / "log1.txt").write_text("x", encoding="utf-8")

    qa_count, logs_count = tm._do_clear_implement()
    assert qa_count == 0
    assert logs_count == 1


def test_do_clear_plan_dir_absent_still_empties_epic(tmp_path):
    config = {"epic": [{"epic_name": "e1"}]}
    count = tm._do_clear_plan(config, tmp_path / "missing_pbi")
    assert count == 0
    assert config["epic"] == []


def test_do_clear_plan_deletes_files_and_empties_epic(tmp_path):
    pbi_dir = tmp_path / "pbi"
    (pbi_dir / "epics").mkdir(parents=True)
    (pbi_dir / "epics" / "e1.md").write_text("x", encoding="utf-8")
    (pbi_dir / "top.md").write_text("x", encoding="utf-8")
    config = {"epic": [{"epic_name": "e1"}, {"epic_name": "e2"}]}

    count = tm._do_clear_plan(config, pbi_dir)

    assert count == 2
    assert config["epic"] == []
    assert list(pbi_dir.iterdir()) == []


def test_do_clear_clarify_keeps_claude_md(tmp_path):
    clar_dir = tmp_path / "clar"
    clar_dir.mkdir()
    (clar_dir / "claude.md").write_text("keep me", encoding="utf-8")
    (clar_dir / "finding.md").write_text("x", encoding="utf-8")
    (clar_dir / "sub").mkdir()

    count = tm._do_clear_clarify(clar_dir)

    assert count == 2
    remaining = [p.name for p in clar_dir.iterdir()]
    assert remaining == ["claude.md"]


def test_do_clear_clarify_only_claude_md_deletes_nothing(tmp_path):
    clar_dir = tmp_path / "clar"
    clar_dir.mkdir()
    (clar_dir / "claude.md").write_text("keep me", encoding="utf-8")

    count = tm._do_clear_clarify(clar_dir)
    assert count == 0


# ---------------------------------------------------------------------------
# _reset_clarify_config_state
# ---------------------------------------------------------------------------

def test_reset_clarify_config_state_clears_tracked_keys():
    config = {
        "last_clarification_action": "evaluate",
        "last_clarification_findings": {"critical": 1},
        "clarify_applied_hashes": {"f.md": "hash"},
        "last_auto_answer": 5,
        "last_clarification_round": 7,
        "last_finalize_round": 3,
        "clarify_session_id": "eval-sid",
        "clarify_session_backend": "claude",
        "clarify_apply_session_id": "apply-sid",
        "clarify_apply_session_backend": "claude",
    }
    tm._reset_clarify_config_state(config)
    assert "last_clarification_action" not in config
    assert "last_clarification_findings" not in config
    assert "clarify_applied_hashes" not in config
    assert "clarify_session_id" not in config
    assert "clarify_session_backend" not in config
    assert "clarify_apply_session_id" not in config
    assert "clarify_apply_session_backend" not in config
    assert config["last_auto_answer"] == 0
    assert config["last_clarification_round"] == 0
    assert config["last_finalize_round"] == 0


def test_reset_clarify_config_state_missing_keys_no_error():
    config = {}
    tm._reset_clarify_config_state(config)
    assert config["last_auto_answer"] == 0
    assert config["last_clarification_round"] == 0
    assert config["last_finalize_round"] == 0


# ---------------------------------------------------------------------------
# _epic_features_actually_done
# ---------------------------------------------------------------------------

def test_epic_features_actually_done_true_when_no_features_key():
    assert tm._epic_features_actually_done({"status": "done"}) is True


def test_epic_features_actually_done_true_when_all_done_and_counts_match():
    epic = {
        "completed_features": 2, "total_features": 2,
        "features": [{"status": "done"}, {"status": "done"}],
    }
    assert tm._epic_features_actually_done(epic) is True


def test_epic_features_actually_done_false_when_a_feature_is_not_done():
    epic = {
        "completed_features": 2, "total_features": 2,
        "features": [{"status": "done"}, {"status": "require_fixing"}],
    }
    assert tm._epic_features_actually_done(epic) is False


def test_epic_features_actually_done_false_when_completed_count_mismatches():
    # The real-world case: epic-level bookkeeping (completed_features) says 0, even though
    # this alone -- without checking every feature's own status -- wouldn't have caught it
    # if a re-implementation round incremented the count without actually finishing them.
    epic = {
        "completed_features": 0, "total_features": 6,
        "features": [{"status": "require_fixing"}] * 6,
    }
    assert tm._epic_features_actually_done(epic) is False


# ---------------------------------------------------------------------------
# reconcile_qa_passed_features
# ---------------------------------------------------------------------------

def test_reconcile_qa_passed_features_marks_leftover_require_fixing_done():
    # The real-world bug: QA failed once (which set every feature to require_fixing and
    # completed_features to 0), the re-implementation round set the epic done without
    # redoing that per-feature bookkeeping, and the next QA passed -- leaving an epic that
    # is "done + QA passed" while all its features still read require_fixing.
    config = {"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 0, "total_features": 6,
        "features": [{"status": "require_fixing"}] * 6,
    }]}
    assert tm.reconcile_qa_passed_features(config) == [("EPIC-04", 6)]
    epic = config["epic"][0]
    assert all(f["status"] == "done" for f in epic["features"])
    assert epic["completed_features"] == 6


def test_reconcile_qa_passed_features_fixes_stale_completed_count_only():
    # completed_features drifted out of sync (here: above total, from a double increment)
    # even though every feature is individually done -- still a contradiction to repair.
    config = {"epic": [{
        "epic_name": "EPIC-10", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 5, "total_features": 4,
        "features": [{"status": "done"}] * 4,
    }]}
    assert tm.reconcile_qa_passed_features(config) == [("EPIC-10", 0)]
    assert config["epic"][0]["completed_features"] == 4


def test_reconcile_qa_passed_features_backfills_missing_total():
    config = {"epic": [{
        "epic_name": "EPIC-11", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 0, "total_features": 0,
        "features": [{"status": "require_fixing"}, {"status": "done"}],
    }]}
    assert tm.reconcile_qa_passed_features(config) == [("EPIC-11", 1)]
    assert config["epic"][0]["total_features"] == 2
    assert config["epic"][0]["completed_features"] == 2


def test_reconcile_qa_passed_features_noop_when_already_consistent():
    config = {"epic": [{
        "epic_name": "EPIC-02", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 2, "total_features": 2,
        "features": [{"status": "done"}, {"status": "done"}],
    }]}
    assert tm.reconcile_qa_passed_features(config) == []


def test_reconcile_qa_passed_features_ignores_epics_that_did_not_pass_qa():
    # Nothing here may be touched: an epic still being worked on, one whose QA FAILED (the
    # require_fixing statuses are the whole point of that state), and one whose QA session
    # is still in flight.
    config = {"epic": [
        {"epic_name": "E1", "status": "on_progress", "qa_passed": False, "qa_status": "idle",
         "completed_features": 0, "total_features": 2,
         "features": [{"status": "require_fixing"}, {"status": "pending"}]},
        {"epic_name": "E2", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
         "completed_features": 0, "total_features": 2,
         "features": [{"status": "require_fixing"}, {"status": "require_fixing"}]},
        {"epic_name": "E3", "status": "done", "qa_passed": True, "qa_status": "ongoing",
         "completed_features": 0, "total_features": 2,
         "features": [{"status": "require_fixing"}, {"status": "done"}]},
    ]}
    assert tm.reconcile_qa_passed_features(config) == []
    assert config["epic"][0]["features"][0]["status"] == "require_fixing"
    assert config["epic"][1]["features"][0]["status"] == "require_fixing"
    assert config["epic"][2]["features"][0]["status"] == "require_fixing"


def test_reconcile_qa_passed_features_and_log_reports_change(capsys):
    config = {"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 0, "total_features": 2,
        "features": [{"status": "require_fixing"}, {"status": "done"}],
    }]}
    assert tm.reconcile_qa_passed_features_and_log(config) is True
    assert "EPIC-04" in capsys.readouterr().out
    assert tm.reconcile_qa_passed_features_and_log(config) is False


# ---------------------------------------------------------------------------
# with_retry_hint
# ---------------------------------------------------------------------------

def test_with_retry_hint_appends_the_way_out():
    reason = tm.with_retry_hint("Something went wrong.")
    assert reason.startswith("Something went wrong.")
    # The dashboard route is the one that matters: a general user shouldn't need a terminal.
    assert "Continue Implementation" in reason
    assert "--reset-failed" in reason


def test_with_retry_hint_is_idempotent():
    """Reasons get folded into one another (see _reason_with_counterpart_context), so the hint
    must not stack up each time one passes through."""
    once = tm.with_retry_hint("Blocked.")
    assert tm.with_retry_hint(once) == once


def test_with_retry_hint_on_an_empty_reason_is_just_the_hint():
    assert tm.with_retry_hint("") == tm.RETRY_HINT
    assert tm.with_retry_hint(None) == tm.RETRY_HINT


# ---------------------------------------------------------------------------
# _reset_failed_epics / _reset_qa_state / _reset_on_progress_epics
# ---------------------------------------------------------------------------

def test_reset_failed_epics_resets_only_failed(isolate_tempa_paths):
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed", "claude_session_id": "abc"},
        {"epic_name": "e2", "status": "done"},
    ]})
    tm._reset_failed_epics()
    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert "claude_session_id" not in saved["epic"][0]
    assert saved["epic"][1]["status"] == "done"


def test_reset_failed_epics_no_failed_epics_noop(isolate_tempa_paths):
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "done"}]})
    tm._reset_failed_epics()
    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "done"


def test_reset_failed_epics_returns_labels_and_mutates_in_place():
    config = {"epic": [
        {"epic_name": "e1", "status": "failed", "session_id": "abc", "session_backend": "claude"},
        {"epic_name": "e2", "status": "on_progress"},
        {"status": "failed"},  # unnamed → falls back to the index label
    ]}
    reset = tm.reset_failed_epics(config)
    assert reset == ["e1", "epic_2"]
    assert config["epic"][0]["status"] == "pending"
    assert "session_id" not in config["epic"][0]
    assert "session_backend" not in config["epic"][0]
    assert config["epic"][1]["status"] == "on_progress"
    assert config["epic"][2]["status"] == "pending"


def test_reset_failed_epics_clears_run_and_stall_counters():
    # Regression: a "reset" epic that had hit max_session_run or the no-forward-progress
    # guard must not immediately re-trip the exact same limit on its very next attempt.
    config = {"epic": [{
        "epic_name": "e1", "status": "failed", "total_run": 30, "qa_total_run": 5,
        "no_progress_rounds": 2, "blocked_reason": "blocked on EPIC-17",
        "blocked_by_epic": "EPIC-17",
    }]}
    tm.reset_failed_epics(config)
    epic = config["epic"][0]
    assert epic["total_run"] == 0
    assert epic["qa_total_run"] == 0
    assert epic["no_progress_rounds"] == 0
    assert epic["qa_loop_strikes"] == 0
    assert "blocked_reason" not in epic
    assert "blocked_by_epic" not in epic


def test_reset_failed_epics_keeps_qa_history_but_marks_a_reset_boundary():
    # The history is the only record a human has that this epic was cycling through QA, so it
    # survives the reset -- a marker is appended instead, which is what makes the loop guard
    # start counting from here rather than instantly re-tripping on the pre-reset rounds.
    config = {"epic": [{
        "epic_name": "e1", "status": "failed", "qa_loop_strikes": 2,
        "qa_history": [
            {"round": 1, "verdict": "fail", "failed": ["F1"]},
            {"round": 2, "verdict": "fail", "failed": ["F2"]},
        ],
    }]}
    tm.reset_failed_epics(config)
    history = config["epic"][0]["qa_history"]
    assert [entry["verdict"] for entry in history] == ["fail", "fail", "reset"]


def test_reset_qa_state_restarts_the_qa_loop_guard_window():
    config = {"epic": [{
        "epic_name": "e1", "status": "done", "qa_passed": True, "qa_status": "done",
        "qa_loop_strikes": 1, "total_features": 1, "completed_features": 1,
        "features": [{"id": "F1", "status": "done"}],
        "qa_history": [{"round": 1, "verdict": "fail", "failed": ["F1"]}],
    }]}
    tm.reset_qa_state(config)
    epic = config["epic"][0]
    assert epic["qa_loop_strikes"] == 0
    assert [entry["verdict"] for entry in epic["qa_history"]] == ["fail", "reset"]


def test_reset_failed_epics_nothing_failed_returns_empty():
    config = {"epic": [{"epic_name": "e1", "status": "done"}]}
    assert tm.reset_failed_epics(config) == []
    assert config["epic"][0]["status"] == "done"


def test_reset_failed_epics_tolerates_missing_status_key():
    config = {"epic": [{"epic_name": "e1"}]}
    assert tm.reset_failed_epics(config) == []


def test_reset_qa_state_resets_matching_epics(isolate_tempa_paths):
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "done", "qa_passed": True, "qa_status": "done",
         "qa_session_id": "s1", "qa_total_run": 3, "qa_report_filename": "r.md"},
        {"epic_name": "e2", "status": "pending"},
    ]})
    tm._reset_qa_state()
    saved = tempa_config.load_config()
    reset_epic = saved["epic"][0]
    assert reset_epic["qa_passed"] is False
    assert reset_epic["qa_status"] == "idle"
    assert reset_epic["qa_session_id"] == ""
    assert reset_epic["qa_total_run"] == 0
    assert reset_epic["qa_report_filename"] == ""
    assert reset_epic["status"] == "done"  # no features to check against — status untouched
    assert saved["epic"][1] == {"epic_name": "e2", "status": "pending"}


def test_reset_qa_state_no_matching_epics_noop(isolate_tempa_paths):
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "pending"}]})
    tm._reset_qa_state()
    saved = tempa_config.load_config()
    assert saved["epic"][0] == {"epic_name": "e1", "status": "pending"}


def test_reset_qa_state_targets_only_the_named_epic():
    config = {"epic": [
        {"epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done"},
        {"epic_name": "EPIC-05", "status": "done", "qa_passed": True, "qa_status": "done"},
    ]}
    reset = tm.reset_qa_state(config, epic_name="EPIC-04")
    assert reset == [("EPIC-04", False)]
    assert config["epic"][0]["qa_passed"] is False
    assert config["epic"][1]["qa_passed"] is True  # untouched


def test_reset_qa_state_reroutes_to_require_fixing_when_features_not_actually_done():
    # The real-world bug this guards against: a re-implementation round set the epic done
    # without finishing each feature's own bookkeeping, then QA passed anyway.
    config = {"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 0, "total_features": 2,
        "features": [{"status": "require_fixing"}, {"status": "require_fixing"}],
    }]}
    reset = tm.reset_qa_state(config, epic_name="EPIC-04")
    assert reset == [("EPIC-04", True)]
    assert config["epic"][0]["status"] == "require_fixing"
    assert config["epic"][0]["qa_passed"] is False


def test_reset_qa_state_keeps_done_status_when_features_are_actually_done():
    config = {"epic": [{
        "epic_name": "EPIC-04", "status": "done", "qa_passed": True, "qa_status": "done",
        "completed_features": 2, "total_features": 2,
        "features": [{"status": "done"}, {"status": "done"}],
    }]}
    reset = tm.reset_qa_state(config, epic_name="EPIC-04")
    assert reset == [("EPIC-04", False)]
    assert config["epic"][0]["status"] == "done"


def test_reset_qa_state_named_epic_not_found_returns_empty():
    config = {"epic": [{"epic_name": "EPIC-04", "status": "done", "qa_passed": True}]}
    assert tm.reset_qa_state(config, epic_name="EPIC-99") == []
    assert config["epic"][0]["qa_passed"] is True  # untouched


def test_reset_on_progress_epics_resets_matching(isolate_tempa_paths):
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "on_progress", "claude_session_id": "abc"},
        {"epic_name": "e2", "status": "pending"},
    ]})
    tm._reset_on_progress_epics()
    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert "claude_session_id" not in saved["epic"][0]
    assert saved["epic"][1]["status"] == "pending"


def test_reset_on_progress_epics_no_matching_epics_noop(isolate_tempa_paths):
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "pending"}]})
    tm._reset_on_progress_epics()
    saved = tempa_config.load_config()
    assert saved["epic"][0] == {"epic_name": "e1", "status": "pending"}


# ---------------------------------------------------------------------------
# run_clarify_clear / run_plan_clear / run_implement_clear / run_clear_all
# ---------------------------------------------------------------------------

def _config_with_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return {"workspace": {"root": str(root)}, "epic": []}


def test_run_clarify_clear_missing_sources_exits_1(isolate_tempa_paths, monkeypatch):
    # get_sources() always synthesizes a non-empty default for "clarifications" from
    # workspace.specs, so the "not configured" guard can't be triggered through config
    # alone — exercise it directly by stubbing get_sources() to return the edge case.
    tempa_config.save_config({"workspace": {"root": ""}})
    monkeypatch.setattr(tm, "get_sources", lambda config: {"clarifications": ""})
    with pytest.raises(SystemExit) as exc:
        tm.run_clarify_clear()
    assert exc.value.code == 1


def test_run_clarify_clear_nothing_to_delete_exits_0(tmp_path, isolate_tempa_paths):
    config = _config_with_workspace(tmp_path)
    tempa_config.save_config(config)
    with pytest.raises(SystemExit) as exc:
        tm.run_clarify_clear()
    assert exc.value.code == 0


def test_run_clarify_clear_with_yes_deletes_files(tmp_path, isolate_tempa_paths, monkeypatch):
    config = _config_with_workspace(tmp_path)
    tempa_config.save_config(config)
    clar_dir = Path(tempa_config.get_sources(config)["clarifications"])
    clar_dir.mkdir(parents=True)
    (clar_dir / "finding.md").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tempa", "clarify", "--clear", "--yes"])
    with pytest.raises(SystemExit) as exc:
        tm.run_clarify_clear()
    assert exc.value.code == 0
    assert list(clar_dir.iterdir()) == []


def test_run_plan_clear_missing_sources_exits_1(isolate_tempa_paths, monkeypatch):
    # As in test_run_clarify_clear_missing_sources_exits_1: get_sources() always
    # synthesizes a non-empty default, so the guard can't be triggered through config
    # alone — stub get_sources() directly to exercise it. (A config-only attempt here
    # previously "passed" for the wrong reason: it fell through to a later crash/abort
    # that also exits 1 but isn't the guard this test claims to cover.)
    tempa_config.save_config({"workspace": {"root": ""}})
    monkeypatch.setattr(tm, "get_sources", lambda config: {"epics": ""})
    with pytest.raises(SystemExit) as exc:
        tm.run_plan_clear()
    assert exc.value.code == 1


def test_run_plan_clear_with_yes_deletes_and_empties_epic(tmp_path, isolate_tempa_paths, monkeypatch):
    config = _config_with_workspace(tmp_path)
    config["epic"] = [{"epic_name": "e1"}]
    tempa_config.save_config(config)
    epics_path = Path(tempa_config.get_sources(config)["epics"])
    epics_path.mkdir(parents=True)
    (epics_path / "e1.md").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tempa", "implement", "--clear-plan", "--yes"])
    with pytest.raises(SystemExit) as exc:
        tm.run_plan_clear()
    assert exc.value.code == 0
    saved = tempa_config.load_config()
    assert saved["epic"] == []


def test_run_implement_clear_nothing_to_clear_exits_0(isolate_tempa_paths):
    tempa_config.save_config({"epic": []})
    with pytest.raises(SystemExit) as exc:
        tm.run_implement_clear()
    assert exc.value.code == 0


def test_run_implement_clear_with_yes_deletes(isolate_tempa_paths, monkeypatch):
    qa_dir = tempa_config.get_qa_dir()
    qa_dir.mkdir(parents=True)
    (qa_dir / "report.md").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tempa", "implement", "--clear", "--yes"])
    with pytest.raises(SystemExit) as exc:
        tm.run_implement_clear()
    assert exc.value.code == 0
    assert list(qa_dir.iterdir()) == []


def test_run_clear_all_missing_epics_exits_1(isolate_tempa_paths, monkeypatch):
    tempa_config.save_config({"workspace": {"root": ""}})
    monkeypatch.setattr(tm, "get_sources", lambda config: {"epics": "", "clarifications": "some/path"})
    with pytest.raises(SystemExit) as exc:
        tm.run_clear_all()
    assert exc.value.code == 1


def test_run_clear_all_missing_clarifications_exits_1(tmp_path, isolate_tempa_paths, monkeypatch):
    config = _config_with_workspace(tmp_path)
    tempa_config.save_config(config)
    real_sources = tempa_config.get_sources(config)
    monkeypatch.setattr(
        tm, "get_sources",
        lambda cfg: {**real_sources, "clarifications": ""},
    )
    with pytest.raises(SystemExit) as exc:
        tm.run_clear_all()
    assert exc.value.code == 1


def test_run_clear_all_nothing_to_clear_exits_0(tmp_path, isolate_tempa_paths):
    config = _config_with_workspace(tmp_path)
    tempa_config.save_config(config)
    with pytest.raises(SystemExit) as exc:
        tm.run_clear_all()
    assert exc.value.code == 0


def test_run_clear_all_stale_state_still_proceeds(tmp_path, isolate_tempa_paths, monkeypatch):
    config = _config_with_workspace(tmp_path)
    config["last_clarification_action"] = "evaluate"
    tempa_config.save_config(config)

    # Nothing physically to delete, but stale clarify-config state must still trigger
    # the confirm+clear path rather than the early "nothing to clear" exit.
    monkeypatch.setattr(sys, "argv", ["tempa", "clear-all", "--yes"])
    with pytest.raises(SystemExit) as exc:
        tm.run_clear_all()
    assert exc.value.code == 0
    saved = tempa_config.load_config()
    assert "last_clarification_action" not in saved


def test_run_clear_all_full_happy_path_with_yes(tmp_path, isolate_tempa_paths, monkeypatch):
    config = _config_with_workspace(tmp_path)
    config["epic"] = [{"epic_name": "e1"}]
    tempa_config.save_config(config)

    sources = tempa_config.get_sources(config)
    qa_dir = tempa_config.get_qa_dir()
    logs_dir = tempa_config.get_logs_dir()
    pbi_dir = Path(sources["epics"]).parent
    clar_dir = Path(sources["clarifications"])
    for d in (qa_dir, logs_dir, pbi_dir, clar_dir):
        d.mkdir(parents=True, exist_ok=True)
    (qa_dir / "r.md").write_text("x", encoding="utf-8")
    (logs_dir / "l.txt").write_text("x", encoding="utf-8")
    (pbi_dir / "e1.md").write_text("x", encoding="utf-8")
    (clar_dir / "f.md").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tempa", "clear-all", "--yes"])
    with pytest.raises(SystemExit) as exc:
        tm.run_clear_all()
    assert exc.value.code == 0

    saved = tempa_config.load_config()
    assert saved["epic"] == []
    assert list(qa_dir.iterdir()) == []
    assert list(logs_dir.iterdir()) == []
    assert list(pbi_dir.iterdir()) == []
    assert list(clar_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# _confirm_destructive
# ---------------------------------------------------------------------------

def test_confirm_destructive_yes_flag_skips_prompt(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tempa", "clear", "--yes"])
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("input() must not be called")))
    tm._confirm_destructive("cancelled")  # should not raise


def test_confirm_destructive_non_tty_exits_1(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tempa", "clear"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        tm._confirm_destructive("cancelled")
    assert exc.value.code == 1


def test_confirm_destructive_tty_yes_answer_returns(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tempa", "clear"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    tm._confirm_destructive("cancelled")  # should not raise


def test_confirm_destructive_tty_other_answer_exits_0(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tempa", "clear"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "no")
    with pytest.raises(SystemExit) as exc:
        tm._confirm_destructive("cancelled")
    assert exc.value.code == 0


def test_confirm_destructive_eof_error_treated_as_cancel(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tempa", "clear"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def _raise_eof(*_a):
        raise EOFError()
    monkeypatch.setattr("builtins.input", _raise_eof)
    with pytest.raises(SystemExit) as exc:
        tm._confirm_destructive("cancelled")
    assert exc.value.code == 0
