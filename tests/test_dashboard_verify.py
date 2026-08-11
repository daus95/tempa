"""Tests for dashboard_verify.py's pure helpers: parsing the tempa:verify-result marker,
building the combined (file-based + in-memory) run list, reading one run's detail, and
deleting a report. The subprocess-spawning _start_verify_run/_stop_verify_run aren't
covered here (no subprocess mocking harness in this suite yet — see test_dashboard_runs.py
for the same carve-out on the clarify/implement equivalents)."""

from __future__ import annotations

from types import SimpleNamespace

import dashboard_verify as dv


def _server(verify_runs=None):
    return SimpleNamespace(verify_runs=verify_runs or {})


def _report(passed=1, warned=0, failed=0, marker=True):
    header = f"<!-- tempa:verify-result passed={passed} warned={warned} failed={failed} -->\n" if marker else ""
    return f"{header}# Verification Report — EPIC-01\n\nSummary (total ✅ / ⚠️ / ❌)\n"


def _live_run(running, returncode=None):
    run = dv._new_verify_run_state()
    run["running"] = running
    run["returncode"] = returncode
    return run


# ---------------------------------------------------------------------------
# _parse_verify_result
# ---------------------------------------------------------------------------
def test_parse_verify_result_reads_the_marker():
    assert dv._parse_verify_result(_report(passed=8, warned=1, failed=0)) == {
        "passed": 8, "warned": 1, "failed": 0,
    }


def test_parse_verify_result_none_when_marker_missing():
    assert dv._parse_verify_result(_report(marker=False)) is None


def test_result_label_passed_only_when_no_warn_or_fail():
    assert dv._result_label({"passed": 3, "warned": 0, "failed": 0}) == "passed"
    assert dv._result_label({"passed": 3, "warned": 1, "failed": 0}) == "issues"
    assert dv._result_label({"passed": 3, "warned": 0, "failed": 1}) == "issues"
    assert dv._result_label(None) is None


def test_format_timestamp():
    assert dv._format_timestamp("20260810_143022") == "2026-08-10 14:30:22"


# ---------------------------------------------------------------------------
# _list_verify_runs
# ---------------------------------------------------------------------------
def test_list_verify_runs_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    assert dv._list_verify_runs(_server()) == []


def test_list_verify_runs_reads_completed_reports_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "EPIC-01-verify-20260810_143022.md").write_text(
        _report(passed=8, warned=0, failed=0), encoding="utf-8")
    rows = dv._list_verify_runs(_server())
    assert len(rows) == 1
    assert rows[0]["epic"] == "EPIC-01"
    assert rows[0]["timestamp"] == "2026-08-10 14:30:22"
    assert rows[0]["status"] == "completed"
    assert rows[0]["result"] == "passed"
    assert rows[0]["id"] == "EPIC-01-verify-20260810_143022.md"


def test_list_verify_runs_result_unknown_without_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "EPIC-01-verify-20260810_143022.md").write_text(
        _report(marker=False), encoding="utf-8")
    rows = dv._list_verify_runs(_server())
    assert rows[0]["result"] is None


def test_list_verify_runs_ignores_files_not_matching_the_naming_convention(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "stray-notes.md").write_text("not a verify report", encoding="utf-8")
    assert dv._list_verify_runs(_server()) == []


def test_list_verify_runs_shows_a_running_epic_with_no_file_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    server = _server({"EPIC-02": _live_run(running=True)})
    rows = dv._list_verify_runs(server)
    assert len(rows) == 1
    assert rows[0] == {"id": "live:EPIC-02", "epic": "EPIC-02", "timestamp": "", "status": "running", "result": None}


def test_list_verify_runs_shows_a_failed_epic_with_no_report(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    server = _server({"EPIC-02": _live_run(running=False, returncode=1)})
    rows = dv._list_verify_runs(server)
    assert rows[0]["status"] == "failed"


def test_list_verify_runs_does_not_duplicate_a_successfully_finished_run(tmp_path, monkeypatch):
    # Once a verify run finishes successfully, its report file on disk is the single
    # source of truth for that run — the in-memory entry (returncode == 0) must not also
    # contribute a second "live" row for the same run.
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "EPIC-01-verify-20260810_143022.md").write_text(_report(), encoding="utf-8")
    server = _server({"EPIC-01": _live_run(running=False, returncode=0)})
    rows = dv._list_verify_runs(server)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_list_verify_runs_sorts_running_and_failed_above_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "EPIC-01-verify-20260810_143022.md").write_text(_report(), encoding="utf-8")
    server = _server({"EPIC-02": _live_run(running=True)})
    rows = dv._list_verify_runs(server)
    assert [r["epic"] for r in rows] == ["EPIC-02", "EPIC-01"]


# ---------------------------------------------------------------------------
# _verify_detail
# ---------------------------------------------------------------------------
def test_verify_detail_for_a_running_epic(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    server = _server({"EPIC-02": _live_run(running=True)})
    detail = dv._verify_detail(server, "live:EPIC-02")
    assert detail["status"] == "running"
    assert detail["content"] is None


def test_verify_detail_for_a_failed_epic(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    server = _server({"EPIC-02": _live_run(running=False, returncode=1)})
    detail = dv._verify_detail(server, "live:EPIC-02")
    assert detail["status"] == "failed"


def test_verify_detail_none_for_unknown_live_epic(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    assert dv._verify_detail(_server(), "live:NOPE") is None


def test_verify_detail_reads_the_report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    (tmp_path / "EPIC-01-verify-20260810_143022.md").write_text(
        _report(passed=1, warned=1, failed=0), encoding="utf-8")
    detail = dv._verify_detail(_server(), "EPIC-01-verify-20260810_143022.md")
    assert detail["epic"] == "EPIC-01"
    assert detail["status"] == "completed"
    assert detail["result"] == "issues"
    assert "Verification Report" in detail["content"]


def test_verify_detail_none_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    assert dv._verify_detail(_server(), "EPIC-01-verify-20260810_143022.md") is None


def test_verify_detail_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    outside = tmp_path.parent / "EPIC-01-verify-20260810_143022.md"
    outside.write_text(_report(), encoding="utf-8")
    assert dv._verify_detail(_server(), "../" + outside.name) is None


# ---------------------------------------------------------------------------
# _delete_verify_run
# ---------------------------------------------------------------------------
def test_delete_verify_run_removes_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    target = tmp_path / "EPIC-01-verify-20260810_143022.md"
    target.write_text(_report(), encoding="utf-8")
    assert dv._delete_verify_run("EPIC-01-verify-20260810_143022.md") is True
    assert not target.exists()


def test_delete_verify_run_rejects_live_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    assert dv._delete_verify_run("live:EPIC-01") is False


def test_delete_verify_run_false_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dv.tempa_config, "get_verify_dir", lambda: tmp_path)
    assert dv._delete_verify_run("EPIC-01-verify-20260810_143022.md") is False
