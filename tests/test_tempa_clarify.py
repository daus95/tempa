"""Tests for tempa_clarify.py's clarification-backlog pre-check (see
_resolve_clarification_backlog, used by `clarify --finalize` before its evaluate/apply
loop starts): _clarification_backlog splits existing clarification result files into
"unanswered" vs "answered but not yet applied", and _fill_unanswered_with_recommendations
mechanically copies each unanswered finding's own Recommendation text into its answer
(no agent/LLM call — the "follow recommendation" resolution)."""

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
    assert items[0].existing_answer == "do the thing"


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
    assert by_id["1"].existing_answer == "rec one"
    assert by_id["2"].existing_answer == "already answered"
    assert by_id["3"].existing_answer == "rec three"


def test_fill_no_markers_form_still_gets_filled(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(
        _item("1", "major", "T", "w", "q", "the recommendation", "", wrap_answer=False),
        encoding="utf-8",
    )
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 1
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    assert items[0].existing_answer == "the recommendation"


def test_fill_across_multiple_files(tmp_path):
    f1 = tmp_path / "a.md"
    f1.write_text(_item("1", "major", "T1", "w", "q", "rec a", ""), encoding="utf-8")
    f2 = tmp_path / "b.md"
    f2.write_text(_item("1", "minor", "T2", "w", "q", "rec b", ""), encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f1, f2])
    assert filled == 2
    items1, _ = tc.parse_file(f1, f1.read_text(encoding="utf-8"), 0)
    items2, _ = tc.parse_file(f2, f2.read_text(encoding="utf-8"), 0)
    assert items1[0].existing_answer == "rec a"
    assert items2[0].existing_answer == "rec b"


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
    assert items[0].existing_answer == "do the thing"


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
# run_clarify_finalize — convergence guard
#
# If apply can't reduce the critical+major count for `finalize_no_progress_rounds`
# rounds in a row, the loop must stop on its own instead of burning the rest of
# max_clarification_run re-evaluating a PRD that apply has no more moves on (the
# remaining findings need a human decision).
# ---------------------------------------------------------------------------

def test_finalize_stops_after_no_progress_rounds(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")},
                              "finalize_no_progress_rounds": 2})

    calls = {"evaluate": 0, "apply": 0}

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        calls["evaluate"] += 1
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 2, "major": 1, "minor": 0}
        tempa_config.save_config(cfg)
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        calls["apply"] += 1
        return True

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # Round 1: 3 findings, no prior round to compare against -> no-progress count stays 0.
    # Round 2: still 3 findings (no reduction) -> no-progress count becomes 1.
    # Round 3: still 3 findings -> no-progress count reaches the configured limit (2) -> stop
    # BEFORE applying again — evaluate ran 3 times, apply only ran after rounds 1 and 2.
    assert calls["evaluate"] == 3
    assert calls["apply"] == 2


def test_finalize_no_progress_limit_defaults_to_five(tmp_path, isolate_tempa_paths, monkeypatch):
    # A config.json without the key (or with a junk value) falls back to the default of 5,
    # so the automation gets five stalled rounds before handing back to a human.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}})

    calls = {"evaluate": 0, "apply": 0}

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        calls["evaluate"] += 1
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 2, "major": 1, "minor": 0}
        tempa_config.save_config(cfg)
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        calls["apply"] += 1
        return True

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    # Same counting as above, five stalled rounds instead of two: rounds 2-6 each add one.
    assert calls["evaluate"] == 6
    assert calls["apply"] == 5


def test_finalize_keeps_going_when_findings_are_decreasing(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}})

    findings_by_round = [
        {"critical": 3, "major": 0, "minor": 0},
        {"critical": 1, "major": 0, "minor": 0},
        {"critical": 0, "major": 0, "minor": 0},  # done: no critical/major left
    ]
    calls = {"evaluate": 0, "apply": 0}

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = findings_by_round[calls["evaluate"]]
        calls["evaluate"] += 1
        tempa_config.save_config(cfg)
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        calls["apply"] += 1
        return True

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 0  # reached 0 critical/0 major -> clean success
    assert calls["evaluate"] == 3
    assert calls["apply"] == 2  # no apply needed after the clean 3rd round


def test_finalize_apply_resumes_evaluate_session(tmp_path, isolate_tempa_paths, monkeypatch):
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({"sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")}})

    seen = {}

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 1, "major": 0, "minor": 0}
        cfg["clarify_session_id"] = "eval-sid-42"
        cfg["clarify_session_backend"] = "claude"
        tempa_config.save_config(cfg)
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        seen["resume_session_id"] = resume_session_id
        return False  # stop after the first round so the test doesn't need round 2

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)

    with pytest.raises(SystemExit) as exc:
        tc.run_clarify_finalize()

    assert exc.value.code == 1
    assert seen["resume_session_id"] == "eval-sid-42"


def test_finalize_apply_does_not_resume_when_clarify_apply_backend_differs(tmp_path, isolate_tempa_paths, monkeypatch):
    # clarify_apply is configured with a different backend than clarify's evaluate
    # session — a session id captured under "claude" is meaningless to "codex", so the
    # apply step must NOT be handed a resume_session_id in this case.
    clar_dir = tmp_path / "clarifications"
    clar_dir.mkdir()
    tempa_config.save_config({
        "sources": {"clarifications": str(clar_dir), "prd": str(tmp_path / "prd")},
        "backends": {"clarify": "claude", "clarify_apply": "codex"},
    })

    seen = {}

    def fake_run_clarification_session(prompt, run_number, backend, model, reasoning_effort=""):
        cfg = tempa_config.load_config()
        cfg["last_clarification_findings"] = {"critical": 1, "major": 0, "minor": 0}
        cfg["clarify_session_id"] = "eval-sid-42"
        cfg["clarify_session_backend"] = "claude"
        tempa_config.save_config(cfg)
        return True

    def fake_run_apply_step(config, resume_session_id=None):
        seen["resume_session_id"] = resume_session_id
        return False

    monkeypatch.setattr(tc, "run_clarification_session", fake_run_clarification_session)
    monkeypatch.setattr(tc, "_run_apply_step", fake_run_apply_step)

    with pytest.raises(SystemExit):
        tc.run_clarify_finalize()

    assert seen["resume_session_id"] is None


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
