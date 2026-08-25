"""Tests for tempa_clarify.py's clarification backlog and the `clarify --finalize` loop.

_clarification_backlog splits existing clarification result files into "unanswered" vs
"answered but not yet applied", and _fill_unanswered_with_recommendations mechanically
marks each unanswered finding as "follow the recommendation" — a mode="recommendation"
marker with an empty body, not a copy of the recommendation text (no agent/LLM call; see
ClarificationItem.resolved_answer, which reconstructs the full text). Both feed
_prepare_finalize_backlog, the pre-flight run before finalize's loop starts.

The finalize tests below cover the loop itself: evaluate -> auto-answer until an
evaluation comes back clean, then ONE apply ("compaction") that writes the accumulated
answers into the PRD, then ONE verification evaluate over the result. No apply runs
inside the loop."""

from __future__ import annotations

import hashlib
import os
import re

import pytest

import tempa_clarify as tc
import tempa_config


def _item(item_id, severity, heading, where, question, recommendation, answer, wrap_answer=True):
    if wrap_answer:
        answer_block = f"<!-- clarify:answer-start -->\n{answer}\n<!-- clarify:answer-end -->"
    else:
        answer_block = answer
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### {heading}\n"
        f"**Where:** {where}\n"
        f"**Question:** {question}\n"
        f"**Recommendation:** {recommendation}\n"
        f"**Your answer:** {answer_block}\n"
        f"<!-- clarify:enditem -->\n"
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# _clarification_backlog
# ---------------------------------------------------------------------------

def test_backlog_empty_dir(tmp_path):
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == []


def test_backlog_unanswered_file_detected(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do X", ""), encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == [f]
    assert unapplied == []


def test_backlog_fully_answered_but_never_applied(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "major", "T", "w", "q", "do X", "already answered")
    f.write_text(text, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == [f]


def test_backlog_fully_answered_and_hash_matches_applied_hashes(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "major", "T", "w", "q", "do X", "already answered")
    f.write_text(text, encoding="utf-8")
    applied_hashes = {"a.md": _hash(text)}
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == []
    assert unapplied == []


def test_backlog_answered_but_content_changed_since_last_apply(tmp_path):
    f = tmp_path / "a.md"
    original = _item("1", "major", "T", "w", "q", "do X", "first answer")
    applied_hashes = {"a.md": _hash(original)}
    edited = _item("1", "major", "T", "w", "q", "do X", "edited answer")
    f.write_text(edited, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == []
    assert unapplied == [f]


def test_backlog_mixed_files_split_correctly(tmp_path):
    unanswered_file = tmp_path / "unanswered.md"
    unanswered_file.write_text(_item("1", "critical", "T1", "w", "q", "rec", ""), encoding="utf-8")

    unapplied_file = tmp_path / "unapplied.md"
    unapplied_file.write_text(_item("1", "major", "T2", "w", "q", "rec", "answer"), encoding="utf-8")

    applied_text = _item("1", "minor", "T3", "w", "q", "rec", "answer")
    applied_file = tmp_path / "applied.md"
    applied_file.write_text(applied_text, encoding="utf-8")

    applied_hashes = {"applied.md": _hash(applied_text)}
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == [unanswered_file]
    assert unapplied == [unapplied_file]


def test_backlog_ignores_claude_md_and_files_with_no_recognized_items(tmp_path):
    (tmp_path / "claude.md").write_text("not a clarification file", encoding="utf-8")
    (tmp_path / "empty.md").write_text("# nothing recognized here\n", encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == []


def test_backlog_file_with_at_least_one_unanswered_item_counts_as_unanswered(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "critical", "T1", "w", "q", "rec1", "answered") + _item(
        "2", "major", "T2", "w", "q", "rec2", ""
    )
    f.write_text(text, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == [f]
    assert unapplied == []


# ---------------------------------------------------------------------------
# _fill_unanswered_with_recommendations
# ---------------------------------------------------------------------------

def test_fill_writes_recommendation_into_empty_answer(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do the thing", ""), encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 1
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    # The recommendation text is NOT copied into the file — only the mode marker is set.
    assert items[0].existing_answer == ""
    assert items[0].answer_mode == "recommendation"
    assert items[0].resolved_answer == "do the thing"


def test_fill_leaves_already_answered_items_untouched(tmp_path):
    f = tmp_path / "a.md"
    original = _item("1", "major", "T", "w", "q", "do the thing", "my own answer")
    f.write_text(original, encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 0
    assert f.read_text(encoding="utf-8") == original


def test_fill_multiple_unanswered_items_in_one_file(tmp_path):
    f = tmp_path / "a.md"
    text = (
        _item("1", "critical", "T1", "w", "q", "rec one", "")
        + _item("2", "major", "T2", "w", "q", "rec two", "already answered")
        + _item("3", "minor", "T3", "w", "q", "rec three", "")
    )
    f.write_text(text, encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 2
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    by_id = {it.raw_id: it for it in items}
    assert by_id["1"].resolved_answer == "rec one"
    assert by_id["1"].answer_mode == "recommendation"
    assert by_id["2"].resolved_answer == "already answered"
    assert by_id["3"].resolved_answer == "rec three"
    assert by_id["3"].answer_mode == "recommendation"


def test_fill_no_markers_form_still_gets_filled(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(
        _item("1", "major", "T", "w", "q", "the recommendation", "", wrap_answer=False),
        encoding="utf-8",
    )
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 1
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    # Upgraded to a mode="recommendation" marker, same as the marker'd branch — no
    # verbatim copy, even for a file that started with no markers at all.
    assert items[0].existing_answer == ""
    assert items[0].answer_mode == "recommendation"
    assert items[0].resolved_answer == "the recommendation"
    assert items[0].has_markers is True


def test_fill_across_multiple_files(tmp_path):
    f1 = tmp_path / "a.md"
    f1.write_text(_item("1", "major", "T1", "w", "q", "rec a", ""), encoding="utf-8")
    f2 = tmp_path / "b.md"
    f2.write_text(_item("1", "minor", "T2", "w", "q", "rec b", ""), encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f1, f2])
    assert filled == 2
    items1, _ = tc.parse_file(f1, f1.read_text(encoding="utf-8"), 0)
    items2, _ = tc.parse_file(f2, f2.read_text(encoding="utf-8"), 0)
    assert items1[0].resolved_answer == "rec a"
    assert items2[0].resolved_answer == "rec b"


def test_fill_then_backlog_reclassifies_as_unapplied(tmp_path):
    """After filling, a file that was "unanswered" should reclassify as
    "unapplied" (fully answered, but not yet reflected in applied_hashes) —
    exactly the handoff _resolve_clarification_backlog relies on before its
    single apply pass."""
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do X", ""), encoding="utf-8")
    tc._fill_unanswered_with_recommendations([f])
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == [f]


# ---------------------------------------------------------------------------
# _stamp_clean_evaluation_if_zero
# ---------------------------------------------------------------------------

def test_stamp_clean_evaluation_all_zero_sets_timestamp():
    config = {}
    tc._stamp_clean_evaluation_if_zero(config, critical=0, major=0, minor=0)
    assert isinstance(config.get("last_clean_evaluation_at"), float)
    assert config["last_clean_evaluation_at"] > 0


def test_stamp_clean_evaluation_any_nonzero_severity_leaves_config_untouched():
    for critical, major, minor in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (2, 3, 4)]:
        config = {}
        tc._stamp_clean_evaluation_if_zero(config, critical, major, minor)
        assert "last_clean_evaluation_at" not in config


# ---------------------------------------------------------------------------
# _clarification_dir_snapshot / _clarification_report_files — which files a
# session actually produced. Judged against a before-snapshot rather than a
# wall-clock cutoff, because the answer UI's "Save & Clarify" rewrites the file
# being answered and starts a run in the same breath, and the old mtime cutoff
# reported that already-answered file as a result of the new run.
# ---------------------------------------------------------------------------

def test_report_files_empty_when_nothing_changed(tmp_path):
    (tmp_path / "clarification-1.md").write_text("old", encoding="utf-8")
    before = tc._clarification_dir_snapshot(tmp_path)
    assert tc._clarification_report_files(tmp_path, before) == []


def test_report_files_detects_a_newly_written_file(tmp_path):
    (tmp_path / "clarification-1.md").write_text("old", encoding="utf-8")
    before = tc._clarification_dir_snapshot(tmp_path)
    (tmp_path / "clarification-2.md").write_text("new", encoding="utf-8")
    assert [p.name for p in tc._clarification_report_files(tmp_path, before)] == [
        "clarification-2.md"
    ]


def test_report_files_detects_an_edited_existing_file(tmp_path):
    f = tmp_path / "clarification-1.md"
    f.write_text("old", encoding="utf-8")
    before = tc._clarification_dir_snapshot(tmp_path)
    f.write_text("old plus an answer", encoding="utf-8")
    assert [p.name for p in tc._clarification_report_files(tmp_path, before)] == [
        "clarification-1.md"
    ]


def test_report_files_detects_a_same_length_rewrite(tmp_path):
    """Pins the mtime half of the snapshot tuple: an agent rewriting an answer in place
    can leave the byte count identical, and a size-only snapshot would miss it entirely.
    utime is set explicitly so the assertion does not depend on the filesystem's
    timestamp granularity."""
    f = tmp_path / "clarification-1.md"
    f.write_text("answer aaa", encoding="utf-8")
    original = f.stat()
    before = tc._clarification_dir_snapshot(tmp_path)
    f.write_text("answer bbb", encoding="utf-8")  # same length, different content
    os.utime(f, (original.st_mtime + 5,) * 2)
    assert f.stat().st_size == original.st_size  # only the mtime differs
    assert [p.name for p in tc._clarification_report_files(tmp_path, before)] == [
        "clarification-1.md"
    ]


def test_report_files_detects_a_rewrite_that_kept_the_original_mtime(tmp_path):
    """Pins the size half: on a filesystem whose timestamp granularity is coarse enough
    to hide a write made in the same tick as the snapshot, only the changed byte count
    is left to notice it."""
    f = tmp_path / "clarification-1.md"
    f.write_text("findings", encoding="utf-8")
    original = f.stat()
    before = tc._clarification_dir_snapshot(tmp_path)
    f.write_text("findings and a much longer answer", encoding="utf-8")
    os.utime(f, (original.st_mtime,) * 2)  # mtime pushed back; only the size differs
    assert f.stat().st_mtime == original.st_mtime
    assert [p.name for p in tc._clarification_report_files(tmp_path, before)] == [
        "clarification-1.md"
    ]


def test_report_files_returns_new_files_sorted(tmp_path):
    before = tc._clarification_dir_snapshot(tmp_path)
    for name in ("clarification-3.md", "clarification-1.md", "clarification-2.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert [p.name for p in tc._clarification_report_files(tmp_path, before)] == [
        "clarification-1.md",
        "clarification-2.md",
        "clarification-3.md",
    ]


def test_report_files_ignores_non_markdown(tmp_path):
    before = tc._clarification_dir_snapshot(tmp_path)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert tc._clarification_report_files(tmp_path, before) == []


def test_snapshot_of_missing_dir_is_empty_and_reports_nothing(tmp_path):
    missing = tmp_path / "nope"
    assert tc._clarification_dir_snapshot(missing) == {}
    assert tc._clarification_report_files(missing, {}) == []


# ---------------------------------------------------------------------------
# _run_apply_step — only sends the apply backlog to the agent (never every
# clarification file ever written), resumes when told to, and cleans up its own
# retry-resume id once done.
# ---------------------------------------------------------------------------

def _config_with_clar_dir(tmp_path, **extra):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    prd_dir = tmp_path / "prd"
    prd_dir.mkdir()
    config = {"sources": {"clarifications": str(clar_dir), "prd": str(prd_dir)}, **extra}
    import tempa_config
    tempa_config.save_config(config)
    return tempa_config.load_config(), clar_dir


def test_run_apply_step_no_backlog_spawns_no_session(tmp_path, isolate_tempa_paths, monkeypatch):
    config, clar_dir = _config_with_clar_dir(tmp_path)
    text = _item("1", "major", "T", "w", "q", "rec", "answered")
    f = clar_dir / "a.md"
    f.write_text(text, encoding="utf-8")
    config["clarify_applied_hashes"] = {"a.md": _hash(text)}

    called = []
    monkeypatch.setattr(tc, "run_apply_clarification_session", lambda *a, **k: called.append((a, k)) or True)

    assert tc._run_apply_step(config) is True
    assert called == []


def test_run_apply_step_sends_only_backlog_files(tmp_path, isolate_tempa_paths, monkeypatch):
    config, clar_dir = _config_with_clar_dir(tmp_path)
    stale_text = _item("1", "minor", "T0", "w", "q", "rec0", "answer0")
    stale = clar_dir / "already-applied.md"
    stale.write_text(stale_text, encoding="utf-8")
    config["clarify_applied_hashes"] = {"already-applied.md": _hash(stale_text)}

    backlog_file = clar_dir / "backlog.md"
    backlog_file.write_text(_item("1", "critical", "T1", "w", "q", "rec1", ""), encoding="utf-8")

    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()

    seen = {}

    def fake_prompt(cfg, files):
        seen["files"] = files
        return "PROMPT"
    monkeypatch.setattr(tc, "build_apply_clarification_prompt", fake_prompt)
    monkeypatch.setattr(tc, "run_apply_clarification_session", lambda *a, **k: True)

    assert tc._run_apply_step(config) is True
    assert seen["files"] == [backlog_file]  # NOT stale (already-applied.md)


def test_run_apply_step_fills_unanswered_findings_with_recommendation_before_applying(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    # Regression: a finding evaluate just wrote (still unanswered) must be filled in with
    # its own Recommendation BEFORE apply runs — otherwise the finding gets resolved in the
    # PRD (apply falls back to Recommendation when "Your answer" is blank) but the
    # clarification file itself is left permanently showing as "Unanswered" in the
    # dashboard, even though finalize already succeeded.
    config, clar_dir = _config_with_clar_dir(tmp_path)
    f = clar_dir / "fresh.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do the thing", ""), encoding="utf-8")

    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()
    monkeypatch.setattr(tc, "run_apply_clarification_session", lambda *a, **k: True)

    assert tc._run_apply_step(config) is True

    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    assert items[0].resolved_answer == "do the thing"
    assert items[0].answer_mode == "recommendation"


def test_run_apply_step_resumes_given_session_id(tmp_path, isolate_tempa_paths, monkeypatch):
    config, clar_dir = _config_with_clar_dir(tmp_path)
    (clar_dir / "a.md").write_text(_item("1", "major", "T", "w", "q", "rec", ""), encoding="utf-8")
    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort="", resume_session_id=None):
        seen["resume_session_id"] = resume_session_id
        return True
    monkeypatch.setattr(tc, "run_apply_clarification_session", fake_session)

    assert tc._run_apply_step(config, resume_session_id="evaluate-sid") is True
    assert seen["resume_session_id"] == "evaluate-sid"


def test_run_apply_step_own_retry_session_id_wins_over_passed_in_one(tmp_path, isolate_tempa_paths, monkeypatch):
    # If THIS apply step already made a partial attempt (captured its own session id
    # into config["clarify_apply_session_id"] — e.g. a usage-limit retry mid-apply), that
    # takes priority over resuming the evaluate session again.
    config, clar_dir = _config_with_clar_dir(
        tmp_path, clarify_apply_session_id="apply-sid", clarify_apply_session_backend="claude",
    )
    (clar_dir / "a.md").write_text(_item("1", "major", "T", "w", "q", "rec", ""), encoding="utf-8")
    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort="", resume_session_id=None):
        seen["resume_session_id"] = resume_session_id
        return True
    monkeypatch.setattr(tc, "run_apply_clarification_session", fake_session)

    assert tc._run_apply_step(config, resume_session_id="evaluate-sid") is True
    assert seen["resume_session_id"] == "apply-sid"


def test_run_apply_step_success_clears_own_retry_session_id(tmp_path, isolate_tempa_paths, monkeypatch):
    config, clar_dir = _config_with_clar_dir(
        tmp_path, clarify_apply_session_id="apply-sid", clarify_apply_session_backend="claude",
    )
    (clar_dir / "a.md").write_text(_item("1", "major", "T", "w", "q", "rec", ""), encoding="utf-8")
    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()
    monkeypatch.setattr(tc, "run_apply_clarification_session", lambda *a, **k: True)

    assert tc._run_apply_step(config) is True
    saved = tempa_config.load_config()
    assert "clarify_apply_session_id" not in saved
    assert "clarify_apply_session_backend" not in saved


def test_run_apply_step_uses_clarify_apply_model(tmp_path, isolate_tempa_paths, monkeypatch):
    config, clar_dir = _config_with_clar_dir(tmp_path, models={"clarify_apply": "claude-haiku-4-5-20251001"})
    (clar_dir / "a.md").write_text(_item("1", "major", "T", "w", "q", "rec", ""), encoding="utf-8")
    import tempa_config
    tempa_config.save_config(config)
    config = tempa_config.load_config()

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort="", resume_session_id=None):
        seen["model"] = model
        return True
    monkeypatch.setattr(tc, "run_apply_clarification_session", fake_session)

    assert tc._run_apply_step(config) is True
    assert seen["model"] == "claude-haiku-4-5-20251001"


def test_run_apply_step_uses_clarify_apply_backend_and_effort_independent_of_clarify(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    # clarify_apply is a full stage: its backend/effort don't have to match clarify's.
    config, clar_dir = _config_with_clar_dir(
        tmp_path,
        backends={"clarify": "claude", "clarify_apply": "codex"},
        reasoning_efforts={"clarify": "high", "clarify_apply": "low"},
    )
    (clar_dir / "a.md").write_text(_item("1", "major", "T", "w", "q", "rec", ""), encoding="utf-8")
    tempa_config.save_config(config)
    config = tempa_config.load_config()

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort="", resume_session_id=None):
        seen["backend"] = backend.name
        seen["reasoning_effort"] = reasoning_effort
        return True
    monkeypatch.setattr(tc, "run_apply_clarification_session", fake_session)

    assert tc._run_apply_step(config) is True
    assert seen["backend"] == "codex"
    assert seen["reasoning_effort"] == "low"


# ---------------------------------------------------------------------------
# run_clarify_finalize
#
# The loop is evaluate -> auto-answer, repeated until an evaluation reports no
# critical/major findings. Only then does it compact (one apply) and verify (one more
# evaluate). The helper below drives that with fakes: `findings_by_round` is what each
# successive evaluate reports, and every evaluate leaves behind a clarification file with
# one unanswered finding so there's something for auto-answer to pick up (the real agent
# writes those files; the fake has to stand in for it).
# ---------------------------------------------------------------------------

def _finalize_harness(monkeypatch, clar_dir, findings_by_round, apply_result=True,
                      gitignore_result=("unchanged", "already tracked"),
                      commit_result=("committed", "abc123")):
    """Wire up fake evaluate/auto-answer/apply/gitignore/commit steps and return the
    call-count dict.

    Counts are keyed "evaluate"/"answer"/"apply"; "resume" records what the compaction
    apply was handed, and "prompts" every evaluate prompt (so a test can prove the overlay
    reached the next round). "commits" records each commit message, and "events" is the
    interleaved order of every durability step, which is what proves
    apply -> ensure-tracked -> commit."""
    calls = {"evaluate": 0, "answer": 0, "apply": 0, "resume": [], "prompts": [],
             "gitignore": 0, "commits": [], "events": []}

    def fake_ensure_prd_tracked(workspace_root):
        calls["gitignore"] += 1
        calls["events"].append("gitignore")
        return gitignore_result

    def fake_commit(workspace_root, message):
        calls["commits"].append(message)
        calls["events"].append("commit")
        return commit_result

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        index = min(calls["evaluate"], len(findings_by_round) - 1)
        findings = findings_by_round[index]
        calls["evaluate"] += 1
        calls["prompts"].append(prompt)
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = findings
        tempa_config.save_config(cfg)
        if findings["critical"] or findings["major"]:
            # Stand in for the agent writing this round's findings file.
            (clar_dir / f"clarification-2026010{calls['evaluate']}-000000.md").write_text(
                _item(f"C{calls['evaluate']}", "critical", f"Finding {calls['evaluate']}",
                      "PRD 1", f"question {calls['evaluate']}", f"recommendation {calls['evaluate']}", ""),
                encoding="utf-8",
            )
        return True

    def fake_auto_answer(config, unanswered_files):
        calls["answer"] += 1
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        calls["apply"] += 1
        calls["resume"].append(resume_session_id)
        calls["events"].append("apply")
        if apply_result:
            # A real apply stamps every current file's hash, which is what empties the
            # overlay — without it the "verify" round would still carry one — and records
            # that the last thing to touch the PRD was an apply, not an evaluation.
            tc._record_clarify_applied_state(tempa_config.load_config(), clar_dir)
            cfg = tempa_config.load_config()
            cfg["last_clarification_action"] = "apply"
            tempa_config.save_config(cfg)
        return apply_result

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_auto_answer_step", fake_auto_answer)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)
    monkeypatch.setattr(tc, "ensure_prd_tracked", fake_ensure_prd_tracked)
    monkeypatch.setattr(tc, "commit_workspace_changes", fake_commit)
    return calls


def _finalize_config(tmp_path, clar_dir, **extra):
    # Checkpoints off unless a test asks for them: they add an apply session (and a commit)
    # every N answering rounds, which would change the round/apply counts every test below
    # asserts. The checkpoint tests pass finalize_checkpoint_rounds through **extra.
    settings = {"finalize_checkpoint_rounds": None, **extra}
    tempa_config.save_config({
        "sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}, **settings,
    })


def test_finalize_answers_without_applying_then_compacts_and_verifies(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 3, "major": 0, "minor": 0},
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},   # clean -> compaction
        {"critical": 0, "major": 0, "minor": 0},   # verification round, also clean -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    # Rounds 1-3 evaluate, round 4 is the verification pass over the compacted PRD.
    assert calls["evaluate"] == 4
    assert calls["answer"] == 2          # only the two rounds that found something
    assert calls["apply"] == 1           # ONE compaction, at the end — not per round
    assert tempa_config.load_config()["last_clarification_action"] == "evaluate"


def test_finalize_stamps_timings_only_on_the_files_each_round_produced(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The directory snapshot has to be taken BEFORE each evaluate session, not after.
    That placement is the entire content of the fix and the helper unit tests above
    cannot see it — they call the helpers directly and never cross a session boundary.

    A file that already exists is rewritten by the pre-loop backlog fill moments before
    round 1 starts, which is the same shape as the answer UI's "Save & Clarify"
    rewriting a file and launching a run in one gesture. It must NOT be stamped with the
    round's duration — that is the corruption the snapshot exists to prevent — while the
    file the round itself wrote must be."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    seeded = clar_dir / "clarification-20251231-000000.md"
    seeded.write_text(
        _item("C0", "critical", "Seeded", "PRD 1", "question 0", "recommendation 0", ""),
        encoding="utf-8",
    )
    _finalize_config(tmp_path, clar_dir)
    _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},   # clean -> compaction
        {"critical": 0, "major": 0, "minor": 0},   # verification round
    ])

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    timings = tempa_config.load_config().get("clarify_file_timings", {})
    assert "clarify_seconds" in timings.get("clarification-20260101-000000.md", {})
    assert "clarify_seconds" not in timings.get(seeded.name, {})


def test_finalize_carries_answers_into_the_next_round_prompt(tmp_path, isolate_tempa_paths, monkeypatch):
    # End-to-end proof of the overlay: an answer recorded during round 1 has to show up in
    # round 2's evaluate prompt, since that's what makes applying-before-continuing
    # unnecessary.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    # The real template, minus everything this test doesn't need — the overlay has to be
    # rendered by the actual prompt builder for this to prove anything.
    (isolate_tempa_paths["prompt_dir"] / "clarification.md").write_text(
        "Re-evaluate ${sources.prd} (${finding_scope}).\n\n${pending_resolutions}\n", encoding="utf-8")
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
    ])

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    # The fake auto-answer writes nothing, so the backstop fill supplies round 1's own
    # recommendation as the answer — which the round-2 prompt must then carry.
    assert "recommendation 1" in calls["prompts"][1]
    assert "DECIDED:" in calls["prompts"][1]
    # ...and the verification round (after the compaction) must NOT: applying emptied it.
    assert "recommendation 1" not in calls["prompts"][2]


def test_finalize_stops_after_no_progress_rounds(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_no_progress_rounds=2)
    calls = _finalize_harness(monkeypatch, clar_dir, [{"critical": 2, "major": 1, "minor": 0}])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # Round 1: 3 findings, no prior round to compare against -> no-progress count stays 0.
    # Round 2: still 3 findings (no reduction) -> no-progress count becomes 1.
    # Round 3: still 3 findings -> reaches the configured limit (2) -> stop BEFORE answering
    # again. Nothing was ever applied: the PRD is only rewritten once the loop comes back clean.
    assert calls["evaluate"] == 3
    assert calls["answer"] == 2
    assert calls["apply"] == 0


def test_finalize_no_progress_limit_defaults_to_five(tmp_path, isolate_tempa_paths, monkeypatch):
    # A config.json without the key (or with a junk value) falls back to the default of 5,
    # so the automation gets five stalled rounds before handing back to a human.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [{"critical": 2, "major": 1, "minor": 0}])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # Same counting as above, five stalled rounds instead of two: rounds 2-6 each add one.
    assert calls["evaluate"] == 6
    assert calls["answer"] == 5
    assert calls["apply"] == 0


def test_finalize_dirty_verification_re_enters_the_loop_then_compacts_again(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    # Apply is an agent rewriting prose, so the verification round can legitimately surface
    # something new. That's not a failure: answer it and compact again (bounded by
    # MAX_COMPACTIONS), rather than leaving a rewritten-but-never-verified PRD behind.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 0, "major": 0, "minor": 0},   # round 1 clean -> compaction #1
        {"critical": 1, "major": 0, "minor": 0},   # round 2 verification is DIRTY
        {"critical": 0, "major": 0, "minor": 0},   # round 3 clean again -> compaction #2
        {"critical": 0, "major": 0, "minor": 0},   # round 4 verification clean -> done
    ])
    # Something has to be pending for round 1's compaction to have work to do.
    (clar_dir / "clarification-20251231-000000.md").write_text(
        _item("C0", "critical", "T", "w", "q", "rec", "decided"), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 4
    assert calls["apply"] == 2


def test_finalize_gives_up_after_max_compactions(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    # Every verification comes back dirty, so the run keeps wanting to compact again.
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
    ])
    (clar_dir / "clarification-20251231-000000.md").write_text(
        _item("C0", "critical", "T", "w", "q", "rec", "decided"), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert calls["apply"] == tc.MAX_COMPACTIONS  # never a third rewrite of the PRD


def test_finalize_verification_does_not_trip_the_convergence_guard(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    # The compaction materially rewrites the PRD, so the verification round's finding count
    # says nothing about whether the loop was making progress before it. Comparing the two
    # would trip the guard immediately at finalize_no_progress_rounds=1.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_no_progress_rounds=1)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},   # round 1
        {"critical": 0, "major": 0, "minor": 0},   # round 2 clean -> compaction
        {"critical": 1, "major": 0, "minor": 0},   # round 3 verification: same total as round 1
        {"critical": 0, "major": 0, "minor": 0},   # round 4 clean -> compaction #2
        {"critical": 0, "major": 0, "minor": 0},   # round 5 verification clean -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 5


def test_finalize_verification_round_counts_against_max_run(tmp_path, isolate_tempa_paths, monkeypatch):
    # The verification pass is a full evaluate session with full cost, so it consumes a
    # round of the budget and shows up in last_finalize_round like any other.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
    ])
    (clar_dir / "clarification-20251231-000000.md").write_text(
        _item("C0", "critical", "T", "w", "q", "rec", "decided"), encoding="utf-8")

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    saved = tempa_config.load_config()
    assert saved["last_finalize_round"] == 2
    assert saved["last_finalize_phase"] == "verify"


def test_finalize_stops_at_max_run_without_compacting(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, max_clarification_run=1)
    calls = _finalize_harness(monkeypatch, clar_dir, [{"critical": 1, "major": 0, "minor": 0}])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert calls["evaluate"] == 1
    assert calls["apply"] == 0


def test_finalize_exits_clean_when_there_is_nothing_to_compact(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [{"critical": 0, "major": 0, "minor": 0}])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 1
    assert calls["apply"] == 0


def test_finalize_auto_answer_failure_stops_the_run(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [{"critical": 1, "major": 0, "minor": 0}])
    monkeypatch.setattr(tc, "_run_auto_answer_step", lambda config, files: False)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert calls["evaluate"] == 1


def test_finalize_apply_resumes_evaluate_session(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(
        monkeypatch, clar_dir,
        [{"critical": 0, "major": 0, "minor": 0}],
        apply_result=False,  # stop right after the compaction; the resume id is all we want
    )
    (clar_dir / "clarification-20251231-000000.md").write_text(
        _item("C0", "critical", "T", "w", "q", "rec", "decided"), encoding="utf-8")

    def stamp_session(prompt, run_number, backend, model, reasoning_effort=""):
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 0, "major": 0, "minor": 0}
        cfg["clarify_session_id"] = "eval-sid-42"
        cfg["clarify_session_backend"] = "claude"
        tempa_config.save_config(cfg)
        calls["evaluate"] += 1
        return True
    monkeypatch.setattr(tc, "run_clarification_session", stamp_session)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # The evaluate session that just ran already read the whole PRD and was handed the
    # entire overlay — exactly what the compaction has to write.
    assert calls["resume"] == ["eval-sid-42"]


def test_finalize_apply_does_not_resume_when_clarify_apply_backend_differs(tmp_path, isolate_tempa_paths, monkeypatch):
    # clarify_apply is configured with a different backend than clarify's evaluate
    # session — a session id captured under "claude" is meaningless to "codex", so the
    # apply step must NOT be handed a resume_session_id in this case.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, backends={"clarify": "claude", "clarify_apply": "codex"})
    calls = _finalize_harness(
        monkeypatch, clar_dir, [{"critical": 0, "major": 0, "minor": 0}], apply_result=False,
    )
    (clar_dir / "clarification-20251231-000000.md").write_text(
        _item("C0", "critical", "T", "w", "q", "rec", "decided"), encoding="utf-8")

    def stamp_session(prompt, run_number, backend, model, reasoning_effort=""):
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 0, "major": 0, "minor": 0}
        cfg["clarify_session_id"] = "eval-sid-42"
        cfg["clarify_session_backend"] = "claude"
        tempa_config.save_config(cfg)
        calls["evaluate"] += 1
        return True
    monkeypatch.setattr(tc, "run_clarification_session", stamp_session)

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    assert calls["resume"] == [None]


# ---------------------------------------------------------------------------
# run_clarify_answer (auto-answer) — only sends files with an unanswered finding
# ---------------------------------------------------------------------------

def test_auto_answer_sends_only_unanswered_files(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}})

    answered_file = clar_dir / "answered.md"
    answered_file.write_text(_item("1", "minor", "T0", "w", "q", "rec0", "already answered"), encoding="utf-8")
    unanswered_file = clar_dir / "unanswered.md"
    unanswered_file.write_text(_item("1", "critical", "T1", "w", "q", "rec1", ""), encoding="utf-8")

    seen = {}

    def fake_prompt(cfg, files):
        seen["files"] = files
        return "PROMPT"
    monkeypatch.setattr(tc, "build_auto_answer_prompt", fake_prompt)
    monkeypatch.setattr(tc, "run_clarification_session", lambda *a, **k: True)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_answer()

    assert exc.value.code == 0
    assert seen["files"] == [unanswered_file]


def test_auto_answer_nothing_to_answer_spawns_no_session(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}})
    (clar_dir / "answered.md").write_text(
        _item("1", "minor", "T0", "w", "q", "rec0", "already answered"), encoding="utf-8")

    called = []
    monkeypatch.setattr(tc, "run_clarification_session", lambda *a, **k: called.append(1) or True)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_answer()

    assert exc.value.code == 0
    assert called == []


def test_auto_answer_uses_clarify_apply_model(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({
        "sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")},
        "models": {"clarify_apply": "claude-haiku-4-5-20251001"},
    })
    (clar_dir / "a.md").write_text(_item("1", "critical", "T1", "w", "q", "rec1", ""), encoding="utf-8")

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort=""):
        seen["model"] = model
        return True
    monkeypatch.setattr(tc, "run_clarification_session", fake_session)

    with pytest.raises(SystemExit):
        tc.run_clarify_answer()

    assert seen["model"] == "claude-haiku-4-5-20251001"


def test_auto_answer_uses_clarify_apply_backend_and_effort(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({
        "sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")},
        "backends": {"clarify": "claude", "clarify_apply": "copilot"},
        "reasoning_efforts": {"clarify": "high", "clarify_apply": "medium"},
    })
    (clar_dir / "a.md").write_text(_item("1", "critical", "T1", "w", "q", "rec1", ""), encoding="utf-8")

    seen = {}

    def fake_session(prompt, run_number, backend, model, reasoning_effort=""):
        seen["backend"] = backend.name
        seen["reasoning_effort"] = reasoning_effort
        return True
    monkeypatch.setattr(tc, "run_clarification_session", fake_session)

    with pytest.raises(SystemExit):
        tc.run_clarify_answer()

    assert seen["backend"] == "copilot"
    assert seen["reasoning_effort"] == "medium"


# ---------------------------------------------------------------------------
# Graceful stop — finalize checks the .tempa/graceful-stop-clarify sentinel at each of
# the three points where it is about to spend another agent session, so a stop lands
# between paid-for sessions instead of killing one mid-flight.
# ---------------------------------------------------------------------------

def test_finalize_clears_a_stale_sentinel_before_starting(tmp_path, isolate_tempa_paths, monkeypatch):
    # A sentinel left behind by a run that was killed outright must not stop the next one.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    tempa_config.request_graceful_stop("clarify")
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},   # clean -> compaction
        {"critical": 0, "major": 0, "minor": 0},   # verification round -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    # Every checkpoint was passed rather than tripped: a full multi-round run happened.
    assert (calls["evaluate"], calls["answer"], calls["apply"]) == (3, 1, 1)


def test_finalize_stops_before_starting_another_round(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 2, "major": 0, "minor": 0},
        {"critical": 1, "major": 0, "minor": 0},
    ])
    real_auto_answer = tc._run_auto_answer_step

    def answer_then_request_stop(config, unanswered_files):
        result = real_auto_answer(config, unanswered_files)
        tempa_config.request_graceful_stop("clarify")
        return result

    monkeypatch.setattr(tc, "_run_auto_answer_step", answer_then_request_stop)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 1  # round 2 never started
    assert calls["answer"] == 1    # round 1's answer step still completed
    assert tempa_config.graceful_stop_requested("clarify") is False


def test_finalize_stops_before_the_compaction_apply(tmp_path, isolate_tempa_paths, monkeypatch):
    # The compaction is a full PRD rewrite — the single most expensive session in the run,
    # and the one most worth not starting once the user has asked to stop.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},   # clean -> would compact next
    ])
    real_session = tc.run_clarification_session

    def evaluate_then_request_stop(prompt, run_number, backend, model, reasoning_effort=""):
        result = real_session(prompt, run_number, backend, model, reasoning_effort)
        if run_number == 2:
            tempa_config.request_graceful_stop("clarify")
        return result

    monkeypatch.setattr(tc, "run_clarification_session", evaluate_then_request_stop)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 2
    assert calls["apply"] == 0     # the PRD was never rewritten
    assert tempa_config.graceful_stop_requested("clarify") is False


def test_finalize_stops_before_the_auto_answer_session(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 2, "major": 0, "minor": 0},
    ])
    real_session = tc.run_clarification_session

    def evaluate_then_request_stop(prompt, run_number, backend, model, reasoning_effort=""):
        result = real_session(prompt, run_number, backend, model, reasoning_effort)
        tempa_config.request_graceful_stop("clarify")
        return result

    monkeypatch.setattr(tc, "run_clarification_session", evaluate_then_request_stop)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["evaluate"] == 1
    assert calls["answer"] == 0
    # The round's own findings were still saved before the stop — this is what makes the
    # stopped run resumable rather than a wasted round.
    assert tempa_config.load_config()["last_finalize_round"] == 1


# ---------------------------------------------------------------------------
# Finalize checkpoints (finalize_checkpoint_rounds) — the periodic
# apply -> back up -> commit save point inside the loop.
# ---------------------------------------------------------------------------

def _dirty(n):
    """n rounds that each still find something, so the loop keeps answering."""
    return [{"critical": 1, "major": 0, "minor": 0}] * n


def _commit_labels(calls):
    """The commits a run made, as short labels — "roundN" per checkpoint and "final" for the
    closing one — so a test can state the whole sequence on one line."""
    labels = []
    for message in calls["commits"]:
        match = re.search(r"checkpoint . round (\d+)", message)
        labels.append(f"round{match.group(1)}" if match else "final")
    return labels


def test_checkpoint_fires_after_the_configured_number_of_answering_rounds(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=2, max_clarification_run=4)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(4))

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    # Never converged, so the run ends on the max-run limit — but two checkpoints ran on the
    # way there, at rounds 2 and 4, without a single clean evaluation.
    assert exc.value.code == 1
    assert calls["evaluate"] == 4
    assert calls["apply"] == 2
    assert calls["commits"] == [
        "tempa: clarification checkpoint — round 2",
        "tempa: clarification checkpoint — round 4",
    ]


def test_checkpoint_order_is_apply_then_ensure_tracked_then_commit(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The ignore rules have to be right before `git add -A` runs, or the commit stages
    everything except the PRD it exists to capture."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=1)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(1))

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    assert calls["events"] == ["apply", "gitignore", "commit"]


def test_checkpoints_do_not_consume_the_compaction_budget(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """MAX_COMPACTIONS bounds the "verification came back dirty, rewrite again" loop. A
    checkpoint is scheduled by a round counter instead, so charging it there would stop a run
    with frequent checkpoints long before its round limit."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=10)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        *_dirty(4),                                # 4 checkpoints — more than MAX_COMPACTIONS
        {"critical": 0, "major": 0, "minor": 0},   # clean -> compaction
        {"critical": 0, "major": 0, "minor": 0},   # verification, clean -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0                     # not the "rewritten too many times" exit
    assert _commit_labels(calls) == ["round1", "round2", "round3", "round4", "final"]


def test_no_checkpoint_when_the_backlog_is_already_applied(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """Nothing to write means no apply session, no duplicate ZIP and no empty commit."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=2)
    # Rounds that report findings but write no clarification file leave the backlog empty.
    calls = _finalize_harness(monkeypatch, clar_dir, [])
    findings = {"critical": 1, "major": 0, "minor": 0}

    def evaluate_without_writing_a_file(prompt, run_number, backend, model, reasoning_effort=""):
        calls["evaluate"] += 1
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = findings
        tempa_config.save_config(cfg)
        return True

    monkeypatch.setattr(tc, "run_clarification_session", evaluate_without_writing_a_file)

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    assert calls["apply"] == 0
    assert calls["commits"] == []


def test_checkpoints_off_never_applies_inside_the_loop(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """finalize_checkpoint_rounds = null is the pre-checkpoint behavior: one apply, at the
    end, and nothing before it."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=None)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        *_dirty(3),
        {"critical": 0, "major": 0, "minor": 0},   # clean -> compaction
        {"critical": 0, "major": 0, "minor": 0},   # verification, clean -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["apply"] == 1                     # the compaction only
    assert _commit_labels(calls) == ["final"]      # ...but the closing commit still happens


@pytest.mark.parametrize("commit_enabled", [True, False])
def test_the_commit_toggle_gates_the_commit_but_never_the_apply(
    tmp_path, isolate_tempa_paths, monkeypatch, commit_enabled,
):
    """Turning committing off still checkpoints — the answers are written into the PRD, they
    just aren't committed. It also must not touch .gitignore: a workspace that opted out of
    Tempa committing for it has no reason to have its ignore rules rewritten."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1,
                     finalize_checkpoint_commit=commit_enabled)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        *_dirty(1),
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    # One apply, at the checkpoint: it emptied the backlog, so the clean round after it had
    # nothing left to compact. The toggle never gates the apply itself either way.
    assert calls["apply"] == 1
    assert _commit_labels(calls) == (["round1", "final"] if commit_enabled else [])
    assert calls["gitignore"] == (2 if commit_enabled else 0)


def test_a_graceful_stop_is_honored_before_the_checkpoint_apply(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(2))
    real_session = tc.run_clarification_session

    def evaluate_then_request_stop(prompt, run_number, backend, model, reasoning_effort=""):
        result = real_session(prompt, run_number, backend, model, reasoning_effort)
        tempa_config.request_graceful_stop("clarify")
        return result

    monkeypatch.setattr(tc, "run_clarification_session", evaluate_then_request_stop)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["apply"] == 0                     # stopped before spending the apply session
    assert calls["commits"] == []


def test_a_failed_checkpoint_apply_stops_the_run(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(3), apply_result=False)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert calls["evaluate"] == 1                  # stopped at the first checkpoint
    assert calls["commits"] == []


def test_a_failed_commit_does_not_stop_the_run(tmp_path, isolate_tempa_paths, monkeypatch):
    """Best-effort: losing hours of clarification work over a missing git identity would cost
    far more than the commit was protecting."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1)
    calls = _finalize_harness(
        monkeypatch, clar_dir,
        [*_dirty(1), {"critical": 0, "major": 0, "minor": 0},
         {"critical": 0, "major": 0, "minor": 0}],
        gitignore_result=("failed", "could not update .gitignore"),
        commit_result=("failed", "git commit failed: no user.email"),
    )

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0                     # the run still finished successfully
    assert _commit_labels(calls) == ["round1", "final"]


def test_a_compaction_resets_the_checkpoint_counter(tmp_path, isolate_tempa_paths, monkeypatch):
    """The compaction already wrote the whole overlay, so nothing has piled up since — a
    checkpoint on the very next round would be a full apply session for one round of answers."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=2, max_clarification_run=6)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        {"critical": 1, "major": 0, "minor": 0},   # 1: answered (1 round since checkpoint)
        {"critical": 0, "major": 0, "minor": 0},   # 2: clean -> compaction, counter reset
        {"critical": 1, "major": 0, "minor": 0},   # 3: dirty verification -> answered (1)
        {"critical": 0, "major": 0, "minor": 0},   # 4: clean -> compaction #2
        {"critical": 0, "major": 0, "minor": 0},   # 5: verification, clean -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    # Without the reset, round 3 would have been the 2nd round since the last checkpoint and
    # fired one — so the only commit before "final" would prove the reset didn't happen.
    assert _commit_labels(calls) == ["final"]


def test_a_checkpoint_skips_one_comparison_but_keeps_the_no_progress_count(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """With checkpoints every round and a no-progress limit of 3, a counter that reset on
    every checkpoint could never reach the limit — the run would only ever stop at max_run."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1,
                     finalize_no_progress_rounds=3, max_clarification_run=20)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(20))

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # Round 1 sets the baseline; rounds 2, 3 and 4 each fail to reduce it, and the third
    # strike stops the run — checkpoints in between don't reset the count or skip a comparison.
    assert calls["evaluate"] == 4


def test_a_successful_run_still_ends_on_an_evaluate_action(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The dashboard's finalize-readiness gate requires last_clarification_action ==
    "evaluate". A checkpoint stamps "apply", so a round has to stamp it back."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1)
    _finalize_harness(monkeypatch, clar_dir, [
        *_dirty(2),
        {"critical": 0, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert tempa_config.load_config()["last_clarification_action"] == "evaluate"


def test_a_run_that_exhausts_max_run_right_after_a_checkpoint_ends_on_apply(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The one case that now ends on "apply" instead of "evaluate". Accepted deliberately: it
    is truthful (the answers really were written), the run failed anyway, and saving that work
    is the point of the checkpoint."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=1)
    _finalize_harness(monkeypatch, clar_dir, _dirty(1))

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert tempa_config.load_config()["last_clarification_action"] == "apply"


def test_the_round_after_a_checkpoint_carries_an_empty_overlay(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """A checkpoint applies everything recorded so far, so the next evaluation must judge the
    PRD on its own rather than re-carrying decisions already written into it."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=2)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(2))
    monkeypatch.setattr(
        tc, "build_clarification_prompt",
        lambda config, pending=None, skip_minor=False: f"pending={len(pending or [])}")

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    # Round 1 starts clean; round 2 follows the checkpoint, so its overlay is empty again.
    assert calls["prompts"] == ["pending=0", "pending=0"]


def test_the_final_commit_runs_on_the_nothing_left_to_apply_exit(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The second success exit: a clean round whose backlog a checkpoint already emptied. It
    leaves the loop from inside _compact_resolutions_into_documents, so it needs the closing
    commit of its own."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1)
    calls = _finalize_harness(monkeypatch, clar_dir, [
        *_dirty(1),                                # -> checkpoint applies the whole backlog
        {"critical": 0, "major": 0, "minor": 0},   # clean, nothing left to apply -> done
    ])

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0
    assert calls["apply"] == 1                     # the checkpoint's — no compaction followed
    assert _commit_labels(calls) == ["round1", "final"]
    assert calls["commits"][-1] == (
        "tempa: clarification finalized — PRD ready for implementation")


def test_a_checkpoint_ensures_the_prd_is_tracked_before_committing(
    tmp_path, isolate_tempa_paths, monkeypatch,
):
    """The PRD lives under .tempa/, which init git-ignores. Without the rules
    ensure_prd_tracked writes, `git add -A` stages everything in the working folder EXCEPT
    the documents the checkpoint exists to capture — so the order here is load-bearing, not
    cosmetic."""
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    _finalize_config(tmp_path, clar_dir, finalize_checkpoint_rounds=1, max_clarification_run=2)
    calls = _finalize_harness(monkeypatch, clar_dir, _dirty(2))

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    assert calls["events"] == ["apply", "gitignore", "commit"] * 2
    assert calls["gitignore"] == 2

