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

def _finalize_harness(monkeypatch, clar_dir, findings_by_round, apply_result=True):
    """Wire up fake evaluate/auto-answer/apply steps and return the call-count dict.

    Counts are keyed "evaluate"/"answer"/"apply"; "resume" records what the compaction
    apply was handed, and "prompts" every evaluate prompt (so a test can prove the overlay
    reached the next round)."""
    calls = {"evaluate": 0, "answer": 0, "apply": 0, "resume": [], "prompts": []}

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
        if apply_result:
            # A real apply stamps every current file's hash, which is what empties the
            # overlay — without it the "verify" round would still carry one.
            tc._record_clarify_applied_state(tempa_config.load_config(), clar_dir)
        return apply_result

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_auto_answer_step", fake_auto_answer)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)
    return calls


def _finalize_config(tmp_path, clar_dir, **extra):
    tempa_config.save_config({
        "sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}, **extra,
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
