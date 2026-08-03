"""Tests for dashboard_runs.py's apply/evaluate auto-chain helper.

_unapplied_answered_count is the pure, easily-testable piece of the apply-loop fix:
whether the dashboard should keep re-running `clarify --apply` (because a fully-answered
file still isn't reflected in config.json's "clarify_applied_hashes") before it's allowed
to chain into a fresh evaluate. The subprocess-spawning worker() itself isn't covered here
(no subprocess mocking harness in this suite yet) — this locks down the decision function
the loop is built on."""

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
