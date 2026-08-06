"""Tests for dashboard_runs.py's pure decision helpers.

_unapplied_answered_count is the pure, easily-testable piece of the apply-loop fix:
whether the dashboard should keep re-running `clarify --apply` (because a fully-answered
file still isn't reflected in config.json's "clarify_applied_hashes") before it's allowed
to chain into a fresh evaluate. The subprocess-spawning worker() itself isn't covered here
(no subprocess mocking harness in this suite yet) — this locks down the decision function
the loop is built on. _implementation_has_started is the same kind of pure decision
function behind the Start/Continue Implementation relabeling."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import dashboard_runs as dr


def _item(item_id, severity, answer):
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### Title\n"
        f"**Where:** here\n"
        f"**Question:** what?\n"
        f"**Recommendation:** do X\n"
        f"**Your answer:** <!-- clarify:answer-start -->\n{answer}\n<!-- clarify:answer-end -->\n"
        f"<!-- clarify:enditem -->\n"
    )


def _write_fully_answered(path: Path) -> None:
    path.write_text(_item("1", "critical", "resolved"), encoding="utf-8")


def _write_unanswered(path: Path) -> None:
    path.write_text(_item("1", "critical", ""), encoding="utf-8")


def test_unapplied_answered_count_zero_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "_load_clarify_applied_hashes", lambda: {})
    server = SimpleNamespace(clar_dir=tmp_path)
    assert dr._unapplied_answered_count(server) == 0


def test_unapplied_answered_count_ignores_unanswered_files(tmp_path, monkeypatch):
    _write_unanswered(tmp_path / "clarification-20260101-000000.md")
    monkeypatch.setattr(dr, "_load_clarify_applied_hashes", lambda: {})
    server = SimpleNamespace(clar_dir=tmp_path)
    assert dr._unapplied_answered_count(server) == 0


def test_unapplied_answered_count_counts_files_missing_from_applied_hashes(tmp_path, monkeypatch):
    _write_fully_answered(tmp_path / "clarification-20260101-000000.md")
    _write_fully_answered(tmp_path / "clarification-20260102-000000.md")
    monkeypatch.setattr(dr, "_load_clarify_applied_hashes", lambda: {})
    server = SimpleNamespace(clar_dir=tmp_path)
    assert dr._unapplied_answered_count(server) == 2


def test_unapplied_answered_count_excludes_files_with_matching_hash(tmp_path, monkeypatch):
    p1 = tmp_path / "clarification-20260101-000000.md"
    p2 = tmp_path / "clarification-20260102-000000.md"
    _write_fully_answered(p1)
    _write_fully_answered(p2)
    applied_hash = hashlib.sha256(p1.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    monkeypatch.setattr(dr, "_load_clarify_applied_hashes", lambda: {p1.name: applied_hash})
    server = SimpleNamespace(clar_dir=tmp_path)
    assert dr._unapplied_answered_count(server) == 1


def test_unapplied_answered_count_recounts_after_answer_edited(tmp_path, monkeypatch):
    p1 = tmp_path / "clarification-20260101-000000.md"
    _write_fully_answered(p1)
    stale_hash = hashlib.sha256(p1.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    monkeypatch.setattr(dr, "_load_clarify_applied_hashes", lambda: {p1.name: stale_hash})
    server = SimpleNamespace(clar_dir=tmp_path)
    assert dr._unapplied_answered_count(server) == 0

    # Editing the recorded answer changes the file's content hash, so the previously
    # recorded "applied" stamp no longer matches — it needs applying again.
    _write_fully_answered(p1)
    p1.write_text(p1.read_text(encoding="utf-8").replace("resolved", "resolved differently"), encoding="utf-8")
    assert dr._unapplied_answered_count(server) == 1


# ---------------------------------------------------------------------------
# _implementation_has_started — Start vs. Continue Implementation
# ---------------------------------------------------------------------------
def test_implementation_not_started_without_any_epic():
    assert dr._implementation_has_started([]) is False


def test_implementation_not_started_for_a_planned_but_unrun_epic():
    # A plan alone isn't a run: every epic still pending, nothing ever executed.
    epics = [{"epic_name": "EPIC-01", "status": "pending"},
             {"epic_name": "EPIC-02", "status": "pending"}]
    assert dr._implementation_has_started(epics) is False


def test_implementation_not_started_when_status_is_missing():
    assert dr._implementation_has_started([{"epic_name": "EPIC-01"}]) is False


def test_implementation_started_for_every_non_pending_status():
    for status in ("on_progress", "done", "require_fixing", "failed"):
        epics = [{"epic_name": "EPIC-01", "status": status},
                 {"epic_name": "EPIC-02", "status": "pending"}]
        assert dr._implementation_has_started(epics) is True, status


def test_implementation_started_when_only_a_last_run_stamp_remains():
    # `implement --reset-failed` flips a failed epic back to pending but leaves its
    # last_run stamp — the run still happened, so the button must stay "Continue".
    epics = [{"epic_name": "EPIC-01", "status": "pending",
              "last_run": "2026-08-06T17:44:30.220335"}]
    assert dr._implementation_has_started(epics) is True


# ---------------------------------------------------------------------------
# _max_clarification_run_change_warning — saving a new Max Clarification Runs
# while `clarify --finalize` is already running (it snapshots that setting at
# process start, so the running loop can't pick the new value up).
# ---------------------------------------------------------------------------
def _server_with_run(running: bool, mode: str | None):
    run = dr._new_clarify_run_state()
    run["running"] = running
    run["mode"] = mode
    return SimpleNamespace(clarify_run=run)


def test_no_warning_when_the_value_did_not_change():
    server = _server_with_run(True, "finalize")
    assert dr._max_clarification_run_change_warning(server, 25, 25) is None


def test_no_warning_when_nothing_is_running():
    server = _server_with_run(False, None)
    assert dr._max_clarification_run_change_warning(server, 25, 10) is None


def test_no_warning_for_runs_that_do_not_read_the_setting():
    # Only `clarify --finalize` loops on max_clarification_run; a plain evaluate or an
    # apply pass is a single session that never looks at it.
    for mode in ("run", "apply"):
        server = _server_with_run(True, mode)
        assert dr._max_clarification_run_change_warning(server, 25, 10) is None, mode


def test_warning_names_both_limits_while_finalize_is_running():
    server = _server_with_run(True, "finalize")
    warning = dr._max_clarification_run_change_warning(server, 25, 10)
    assert warning is not None
    assert "10" in warning and "25" in warning
    assert "next Finalized Clarification run" in warning


def test_warning_survives_a_missing_previous_value():
    # config.json hand-edited to drop the key entirely — still worth warning, just
    # without a concrete old number to quote.
    server = _server_with_run(True, "finalize")
    warning = dr._max_clarification_run_change_warning(server, None, 10)
    assert warning is not None
    assert "its original limit" in warning


def test_warning_also_fires_when_the_limit_is_raised():
    # Raising it mid-run is the same trap in the other direction: the running loop still
    # stops at the old, lower limit.
    server = _server_with_run(True, "finalize")
    assert dr._max_clarification_run_change_warning(server, 10, 25) is not None


def test_implementation_has_started_ignores_malformed_entries():
    assert dr._implementation_has_started(["EPIC-01", None]) is False


def test_implementation_has_started_reads_config_when_no_epics_passed(monkeypatch):
    monkeypatch.setattr(dr, "_epic_sessions", lambda: [{"status": "done"}])
    assert dr._implementation_has_started() is True
