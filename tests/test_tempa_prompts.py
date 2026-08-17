"""Tests for tempa_prompts.py — prompt template loading and ${...} substitution.

Uses synthetic templates written into the isolated PROMPT_DIR (see conftest.py) rather
than the real src/prompt/*.md files, so template-wording changes don't make these tests
brittle. A separate smoke test at the bottom checks the real shipped templates exist."""

from __future__ import annotations

from pathlib import Path

import pytest

import tempa_config
import tempa_prompts as tp


def _write_prompt(prompt_dir: Path, name: str, content: str) -> None:
    (prompt_dir / f"{name}.md").write_text(content, encoding="utf-8")


def _sample_config(tmp_path, **overrides):
    config = {
        "workspace": {"root": str(tmp_path / "workspace")},
        "models": dict(tempa_config.DEFAULT_MODELS),
        "epic": [],
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

def test_load_prompt_existing_file_verbatim(isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "Hello ${epic}!")
    assert tp.load_prompt("implementation") == "Hello ${epic}!"


def test_load_prompt_missing_uses_fallback(isolate_tempa_paths):
    assert tp.load_prompt("nonexistent", fallback="fallback text") == "fallback text"


def test_load_prompt_missing_no_fallback_returns_empty(isolate_tempa_paths, capsys):
    assert tp.load_prompt("nonexistent") == ""


# ---------------------------------------------------------------------------
# _substitute
# ---------------------------------------------------------------------------

def test_substitute_single_placeholder():
    assert tp._substitute("Hi ${name}", {"name": "World"}) == "Hi World"


def test_substitute_repeated_placeholder_all_replaced():
    assert tp._substitute("${x} and ${x}", {"x": "A"}) == "A and A"


def test_substitute_unknown_key_left_untouched():
    assert tp._substitute("Hi ${name}", {}) == "Hi ${name}"


def test_substitute_extra_param_is_noop():
    assert tp._substitute("Hi", {"unused": "value"}) == "Hi"


# ---------------------------------------------------------------------------
# _principles_block / build_prompt
# ---------------------------------------------------------------------------

def test_principles_block_empty_when_unset(isolate_tempa_paths):
    assert tp._principles_block() == ""


def test_principles_block_wraps_content(isolate_tempa_paths):
    path = tempa_config.get_principles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Use dependency injection.", encoding="utf-8")

    block = tp._principles_block()
    assert "Use dependency injection." in block
    assert "ARCHITECTURE PRINCIPLES" in block


def test_build_prompt_no_principles_equals_substituted_template(isolate_tempa_paths):
    result = tp.build_prompt("Hello ${name}", {"name": "World"})
    assert result == "Hello World"


def test_build_prompt_principles_precede_template(isolate_tempa_paths):
    path = tempa_config.get_principles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Rule one.", encoding="utf-8")

    result = tp.build_prompt("Hello ${name}", {"name": "World"})
    assert result.index("Rule one.") < result.index("Hello World")


# ---------------------------------------------------------------------------
# _resolve_template_params
# ---------------------------------------------------------------------------

def test_resolve_template_params_contains_expected_keys(tmp_path, isolate_tempa_paths):
    config = _sample_config(tmp_path)
    params = tp._resolve_template_params(config, "my-epic")

    from tempa_config import get_config_path, get_sources
    sources = get_sources(config)
    assert params["epic"] == "my-epic"
    assert params["sources"] == "\n".join(sources.values())
    assert params["config_path"] == str(get_config_path())
    for key, value in sources.items():
        assert params[f"sources.{key}"] == value


# ---------------------------------------------------------------------------
# _build_features_block
# ---------------------------------------------------------------------------

def test_build_features_block_epic_not_found():
    config = {"epic": [{"epic_name": "other", "features": [{"id": "f1", "status": "pending", "name": "X"}]}]}
    assert tp._build_features_block(config, "missing-epic") == ""


def test_build_features_block_empty_features_list():
    config = {"epic": [{"epic_name": "e1", "features": []}]}
    assert tp._build_features_block(config, "e1") == ""


def test_build_features_block_all_statuses():
    config = {"epic": [{"epic_name": "e1", "features": [
        {"id": "f1", "status": "done", "name": "Done feature"},
        {"id": "f2", "status": "require_fixing", "name": "Fix feature"},
        {"id": "f3", "status": "pending", "name": "Pending feature"},
    ]}]}
    block = tp._build_features_block(config, "e1")
    assert "Already done" in block
    assert "Needs fixing" in block
    assert "Needs implementing" in block
    assert block.index("Already done") < block.index("Needs fixing") < block.index("Needs implementing")
    assert "✅ f1 — Done feature" in block
    assert "🔧 f2 — Fix feature" in block
    assert "⬜ f3 — Pending feature" in block


def test_build_features_block_only_pending():
    config = {"epic": [{"epic_name": "e1", "features": [
        {"id": "f1", "status": "pending", "name": "Pending feature"},
    ]}]}
    block = tp._build_features_block(config, "e1")
    assert "Already done" not in block
    assert "Needs fixing" not in block
    assert "Needs implementing" in block


# ---------------------------------------------------------------------------
# _build_qa_report_section
# ---------------------------------------------------------------------------

def test_build_qa_report_section_epic_not_found():
    assert tp._build_qa_report_section({"epic": []}, "e1") == ""


def test_build_qa_report_section_no_filename():
    config = {"epic": [{"epic_name": "e1", "qa_report_filename": ""}]}
    assert tp._build_qa_report_section(config, "e1") == ""


def test_build_qa_report_section_file_missing_on_disk(tmp_path):
    config = {"epic": [{"epic_name": "e1", "qa_report_filename": str(tmp_path / "missing.md")}]}
    assert tp._build_qa_report_section(config, "e1") == ""


def test_build_qa_report_section_file_exists(tmp_path):
    report = tmp_path / "qa_report.md"
    report.write_text("QA findings", encoding="utf-8")
    config = {"epic": [{"epic_name": "e1", "qa_report_filename": str(report)}]}
    section = tp._build_qa_report_section(config, "e1")
    assert str(report) in section
    assert "MUST BE READ" in section


def test_build_qa_report_section_skipped_once_qa_has_passed(tmp_path):
    """A report is written on every round now, a passing one included — so its existence alone
    no longer means there are findings, and a later session must not be sent chasing them."""
    report = tmp_path / "qa_report.md"
    report.write_text("All clear, two advisory notes", encoding="utf-8")
    config = {"epic": [{"epic_name": "e1", "qa_report_filename": str(report), "qa_passed": True}]}
    assert tp._build_qa_report_section(config, "e1") == ""


def test_build_qa_report_section_marks_advisory_notes_as_not_findings(tmp_path):
    report = tmp_path / "qa_report.md"
    report.write_text("QA findings", encoding="utf-8")
    config = {"epic": [{"epic_name": "e1", "qa_report_filename": str(report)}]}
    assert "advisory" in tp._build_qa_report_section(config, "e1")


# ---------------------------------------------------------------------------
# _qa_report_staleness_note — an epic stuck on one finding is re-fed the same report
# ---------------------------------------------------------------------------

def test_staleness_note_is_silent_on_a_report_that_predates_nothing():
    assert tp._qa_report_staleness_note({"qa_completed_features": 5, "completed_features": 5}) == ""


def test_staleness_note_is_silent_when_no_qa_round_has_been_stamped_yet():
    """Epics QA'd before this field existed have no baseline — saying nothing is right, since
    claiming staleness we can't measure would be worse than the status quo."""
    assert tp._qa_report_staleness_note({"completed_features": 6}) == ""


def test_staleness_note_never_reads_a_shrinking_count_as_stale():
    """A QA round that fails features lowers completed_features below the previous stamp; that's
    the report being APPLIED, not overtaken."""
    assert tp._qa_report_staleness_note({"qa_completed_features": 6, "completed_features": 5}) == ""


def test_staleness_note_reports_the_features_completed_since_the_report():
    note = tp._qa_report_staleness_note({"qa_completed_features": 5, "completed_features": 6})
    assert "OUT OF DATE" in note
    assert "1 feature completed" in note
    assert "5 of this epic's features" in note and "6 are done now" in note


def test_staleness_note_pluralises_and_still_demands_the_live_findings(tmp_path):
    note = tp._qa_report_staleness_note({"qa_completed_features": 2, "completed_features": 5})
    assert "3 features completed" in note
    # It must not read as permission to skip the report wholesale.
    assert "must still be fixed" in note


def test_build_qa_report_section_carries_the_staleness_note(tmp_path):
    report = tmp_path / "qa_report.md"
    report.write_text("QA findings", encoding="utf-8")
    config = {"epic": [{
        "epic_name": "e1", "qa_report_filename": str(report),
        "qa_completed_features": 5, "completed_features": 6,
    }]}
    section = tp._build_qa_report_section(config, "e1")
    assert "MUST be fixed" in section
    assert "OUT OF DATE" in section


def _epic_with_reports_on_disk(tmp_path, count: int) -> tuple[dict, list]:
    """An epic whose qa_history holds `count` failed rounds, each with a report on disk."""
    reports = []
    for i in range(1, count + 1):
        report = tmp_path / f"qa_round{i}.md"
        report.write_text(f"round {i} findings", encoding="utf-8")
        reports.append(report)
    epic = {
        "epic_name": "e1",
        "qa_report_filename": str(reports[-1]),
        "qa_history": [
            {"round": i + 1, "verdict": "fail", "failed": ["F1"], "report": str(path)}
            for i, path in enumerate(reports)
        ],
    }
    return epic, reports


def test_build_qa_report_section_lists_the_earlier_rounds_as_settled(tmp_path):
    """A session only ever saw the newest report, so it could reroute control flow around a code
    path an earlier round had already fixed and re-break that finding — how EPIC-14 started
    cycling. The older reports now ride along, marked closed rather than to-fix."""
    epic, reports = _epic_with_reports_on_disk(tmp_path, 3)
    section = tp._build_qa_report_section({"epic": [epic]}, "e1")

    assert "DO NOT RE-BREAK" in section
    assert str(reports[0]) in section and str(reports[1]) in section
    # The newest is already listed in full above as the round to fix — not twice.
    assert section.count(str(reports[2])) == 1
    # Oldest first: round 1's findings are the ones most likely to be undone unnoticed, so they
    # must not be skimmed past (or dropped entirely by a "keep it short" cap).
    assert section.index(str(reports[0])) < section.index(str(reports[1]))


def test_build_qa_report_section_has_no_settled_block_on_the_first_round(tmp_path):
    epic, _ = _epic_with_reports_on_disk(tmp_path, 1)
    section = tp._build_qa_report_section({"epic": [epic]}, "e1")

    assert "DO NOT RE-BREAK" not in section
    assert "MUST BE READ" in section


def test_build_qa_report_section_skips_earlier_reports_no_longer_on_disk(tmp_path):
    """qa_history outlives the files it names once a workspace is cleaned. Naming a path that
    isn't there spends a session's time on a failed read."""
    epic, reports = _epic_with_reports_on_disk(tmp_path, 3)
    reports[0].unlink()
    section = tp._build_qa_report_section({"epic": [epic]}, "e1")

    assert str(reports[0]) not in section
    assert str(reports[1]) in section


# ---------------------------------------------------------------------------
# _build_previous_qa_findings
# ---------------------------------------------------------------------------

def _epic_with_rounds(reports: list[str], name: str = "e1", **extra) -> dict:
    history = [
        {"round": i, "verdict": "fail", "failed": ["F1"], "report": report}
        for i, report in enumerate(reports, 1)
    ]
    return {"epic": [{"epic_name": name, "qa_history": history, **extra}]}


def test_previous_qa_findings_says_first_round_when_there_is_no_history(tmp_path):
    block = tp._build_previous_qa_findings({"epic": []}, "e1", tmp_path / "round1.md")
    assert "first QA round" in block


def test_previous_qa_findings_points_at_the_previous_report(tmp_path):
    previous = tmp_path / "round1.md"
    previous.write_text("findings", encoding="utf-8")
    config = _epic_with_rounds([str(previous)])

    block = tp._build_previous_qa_findings(config, "e1", tmp_path / "round2.md")

    assert str(previous) in block
    assert "Re-verify every" in block
    # Advisory notes from that round are settled — re-raising them is the loop this prevents.
    assert "Do NOT re-raise" in block


def test_previous_qa_findings_ignores_a_report_that_is_gone_from_disk(tmp_path):
    config = _epic_with_rounds([str(tmp_path / "deleted.md")])
    assert "first QA round" in tp._build_previous_qa_findings(config, "e1", tmp_path / "r2.md")


def test_previous_qa_findings_never_points_a_round_at_its_own_report(tmp_path):
    """A resumed QA session reuses the previous round's file name — pointing it at that file
    would have it grade its own work in progress as though a finished round had produced it."""
    current = tmp_path / "round1.md"
    current.write_text("half written", encoding="utf-8")
    config = _epic_with_rounds([str(current)])

    assert "first QA round" in tp._build_previous_qa_findings(config, "e1", current)


# ---------------------------------------------------------------------------
# build_session_prompt
# ---------------------------------------------------------------------------

def test_build_session_prompt_not_continuation_uses_implementation(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL ${epic}")
    config = _sample_config(tmp_path)
    prompt = tp.build_session_prompt(config, "e1", is_continuation=False)
    assert "IMPL e1" in prompt


def test_build_session_prompt_continuation_uses_continuation_template(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "continuation", "CONT ${epic}")
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL ${epic}")
    config = _sample_config(tmp_path)
    prompt = tp.build_session_prompt(config, "e1", is_continuation=True)
    assert "CONT e1" in prompt


def test_build_session_prompt_continuation_falls_back_to_implementation(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL ${epic}")
    config = _sample_config(tmp_path)
    prompt = tp.build_session_prompt(config, "e1", is_continuation=True)
    assert "IMPL e1" in prompt


def test_build_session_prompt_features_per_session_rule(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, features_per_session=2)
    prompt = tp.build_session_prompt(config, "e1")
    assert "Limit for this session: at most 2 feature(s)" in prompt


def test_build_session_prompt_no_features_per_session_rule(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, features_per_session=None)
    prompt = tp.build_session_prompt(config, "e1")
    assert "AFTER THE ENTIRE EPIC IS DONE" in prompt


def test_build_session_prompt_features_override_zero_treated_as_no_limit(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, features_per_session=5)
    # features_override=0 is falsy, so `features_per_session if features_override is not
    # None else config.get(...)` picks 0 — and `if features_per_session:` then treats 0
    # as "no limit configured", taking the "AFTER THE ENTIRE EPIC IS DONE" branch.
    prompt = tp.build_session_prompt(config, "e1", features_override=0)
    assert "AFTER THE ENTIRE EPIC IS DONE" in prompt
    assert "Limit for this session" not in prompt


# ---------------------------------------------------------------------------
# build_qa_prompt
# ---------------------------------------------------------------------------

def test_build_qa_prompt_not_continuation(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "qa", "QA ${qa_output_file}")
    config = _sample_config(tmp_path)
    prompt = tp.build_qa_prompt(config, "e1", Path("out.md"), is_continuation=False)
    assert "QA out.md" in prompt


def test_build_qa_prompt_continuation_uses_qa_continuation(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "qa_continuation", "QACONT ${qa_output_file}")
    _write_prompt(isolate_tempa_paths["prompt_dir"], "qa", "QA ${qa_output_file}")
    config = _sample_config(tmp_path)
    prompt = tp.build_qa_prompt(config, "e1", Path("out.md"), is_continuation=True)
    assert "QACONT out.md" in prompt


def test_build_qa_prompt_continuation_falls_back_to_qa(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "qa", "QA ${qa_output_file}")
    config = _sample_config(tmp_path)
    prompt = tp.build_qa_prompt(config, "e1", Path("out.md"), is_continuation=True)
    assert "QA out.md" in prompt


# ---------------------------------------------------------------------------
# build_clarification_prompt / build_apply_clarification_prompt / build_auto_answer_prompt
# ---------------------------------------------------------------------------

def test_build_clarification_prompt_narrow_params(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification",
                  "PRD=${sources.prd} CLAR=${sources.clarifications}")
    config = _sample_config(tmp_path)
    prompt = tp.build_clarification_prompt(config)
    from tempa_config import get_sources
    sources = get_sources(config)
    assert f"PRD={sources['prd']}" in prompt
    assert f"CLAR={sources['clarifications']}" in prompt


def test_build_apply_clarification_prompt(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "apply_clarification", "APPLY ${sources.prd} FILES:${clarification_files}")
    config = _sample_config(tmp_path)
    files = [tmp_path / "clarification-20260101-000000.md", tmp_path / "clarification-20260102-000000.md"]
    prompt = tp.build_apply_clarification_prompt(config, files)
    assert "APPLY " in prompt
    assert "${sources.prd}" not in prompt
    for f in files:
        assert str(f) in prompt


def test_build_apply_clarification_prompt_empty_files(tmp_path, isolate_tempa_paths):
    """No backlog files -> the placeholder substitutes to an empty string, not left
    unresolved — build_apply_clarification_prompt always receives an explicit list now
    (see _run_apply_step), never "read everything in the folder" implicitly."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "apply_clarification", "FILES:[${clarification_files}]")
    config = _sample_config(tmp_path)
    prompt = tp.build_apply_clarification_prompt(config, [])
    assert "FILES:[]" in prompt


def test_build_auto_answer_prompt(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "auto_answer", "AUTO ${sources.clarifications} FILES:${clarification_files}")
    config = _sample_config(tmp_path)
    files = [tmp_path / "clarification-20260101-000000.md"]
    prompt = tp.build_auto_answer_prompt(config, files)
    assert "AUTO " in prompt
    assert "${sources.clarifications}" not in prompt
    assert str(files[0]) in prompt


# ---------------------------------------------------------------------------
# _plan_epics_params / build_plan_epics_prompt / build_review_epics_prompt
# ---------------------------------------------------------------------------

def test_plan_epics_params_narrower_than_resolve_template_params(tmp_path, isolate_tempa_paths):
    config = _sample_config(tmp_path)
    params = tp._plan_epics_params(config)
    assert set(params.keys()) == {"sources.prd", "sources.docs", "sources.epics", "sources.apps", "config_path"}


def test_build_plan_epics_prompt(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "plan_epics", "PLAN ${sources.apps}")
    config = _sample_config(tmp_path)
    prompt = tp.build_plan_epics_prompt(config)
    assert "PLAN " in prompt
    assert "${sources.apps}" not in prompt


def test_build_review_epics_prompt(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "review_epics", "REVIEW ${sources.docs}")
    config = _sample_config(tmp_path)
    prompt = tp.build_review_epics_prompt(config)
    assert "REVIEW " in prompt
    assert "${sources.docs}" not in prompt


# ---------------------------------------------------------------------------
# Smoke test: the real shipped templates (not the synthetic fixtures above)
# ---------------------------------------------------------------------------

def test_real_prompt_files_load():
    real_prompt_dir = Path(__file__).resolve().parent.parent / "src" / "prompt"
    names = [
        "implementation", "continuation", "qa", "qa_continuation", "clarification",
        "apply_clarification", "auto_answer", "plan_epics", "review_epics",
    ]
    for name in names:
        path = real_prompt_dir / f"{name}.md"
        assert path.exists(), f"missing prompt template: {path}"
        assert path.read_text(encoding="utf-8").strip() != "", f"empty prompt template: {path}"


# ---------------------------------------------------------------------------
# ${pending_resolutions} — the already-decided-but-unapplied overlay carried into
# every clarification evaluation (see pending_resolutions in dashboard_clarify_parse.py).
# ---------------------------------------------------------------------------

def _pending(round_index, raw_id, question, answer, title="T", where="PRD 1", severity="critical"):
    from dashboard_clarify_parse import PendingResolution
    return PendingResolution(
        file_name=f"clarification-2026010{round_index}-090000.md", started_at=1767250800.0,
        round_index=round_index, raw_id=raw_id, severity=severity, title=title,
        where=where, question=question, answer=answer,
    )


def test_render_pending_overlay_empty_is_an_explicit_line():
    # Never an empty string: the template has a header above this placeholder, and a
    # dangling header with nothing under it reads as a truncated prompt.
    rendered = tp._render_pending_overlay([])
    assert "None" in rendered
    assert rendered.strip() != ""


def test_build_clarification_prompt_renders_questions_and_answers_verbatim(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "OVERLAY:\n${pending_resolutions}")
    config = _sample_config(tmp_path)
    prompt = tp.build_clarification_prompt(config, pending=[
        _pending(1, "C1", "Should tokens expire?", "Yes — 30 days, rotating on every use."),
    ])
    assert "Should tokens expire?" in prompt
    assert "Yes — 30 days, rotating on every use." in prompt
    assert "[C1] critical — T" in prompt
    assert "DECIDED:" in prompt


def test_build_clarification_prompt_rounds_rendered_in_given_order(tmp_path, isolate_tempa_paths):
    # The order IS the "a later round supersedes an earlier one" rule, so it must survive
    # rendering exactly as handed in.
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${pending_resolutions}")
    prompt = tp.build_clarification_prompt(_sample_config(tmp_path), pending=[
        _pending(1, "C1", "q1", "older decision"),
        _pending(2, "C1", "q2", "newer decision"),
    ])
    assert "ROUND 1 of 2" in prompt
    assert "ROUND 2 of 2" in prompt
    assert prompt.index("older decision") < prompt.index("newer decision")


def test_build_clarification_prompt_without_pending_resolves_the_placeholder(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${pending_resolutions}")
    config = _sample_config(tmp_path)
    for prompt in (tp.build_clarification_prompt(config),
                   tp.build_clarification_prompt(config, pending=[])):
        assert "${pending_resolutions}" not in prompt
        assert "None" in prompt


def test_build_clarification_prompt_never_truncates_a_multiline_answer(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${pending_resolutions}")
    answer = "First line of the decision.\nSecond line with a caveat.\nThird line."
    prompt = tp.build_clarification_prompt(_sample_config(tmp_path), pending=[
        _pending(1, "C1", "q", answer),
    ])
    for line in answer.splitlines():
        assert line in prompt


def test_real_clarification_template_carries_the_overlay_placeholder():
    # Guards against a future edit dropping the placeholder: without it the overlay is
    # silently never sent and clarification quietly re-raises settled findings.
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "src" / "prompt" / "clarification.md").read_text(
        encoding="utf-8")
    assert template.count("${pending_resolutions}") == 1


def _real_template(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "src" / "prompt" / f"{name}.md").read_text(
        encoding="utf-8")


@pytest.mark.parametrize("name", ["qa", "qa_continuation"])
def test_real_qa_templates_carry_the_previous_findings_placeholder(name):
    # Without it each QA round forms a fresh opinion of the epic, flags a different subset of
    # features, and the loop guard reads that shifting subset as an epic cycling through QA.
    assert _real_template(name).count("${previous_qa_findings}") == 1


@pytest.mark.parametrize("name", ["qa", "qa_continuation"])
def test_real_qa_templates_keep_advisory_findings_non_blocking(name):
    # The gate has to distinguish "the behaviour is wrong" from "I'd have written another test".
    # Collapsing the two is what makes a detailed epic impossible to ever pass.
    template = _real_template(name)
    assert "📝" in template
    assert "NOT A QA FAILURE" in template


@pytest.mark.parametrize("name", ["qa", "qa_continuation"])
def test_real_qa_templates_forbid_writing_runner_owned_fields(name):
    template = _real_template(name)
    assert "NEVER WRITE" in template
    for field in ("qa_history", "qa_loop_strikes", "blocked_reason"):
        assert field in template


# ---------------------------------------------------------------------------
# _blocked_feature_block / the ⛔ bucket — the "a human must decide" path
# ---------------------------------------------------------------------------

def _blocked_epic(answer=""):
    return {
        "epic_name": "e1", "status": "on_progress",
        "features": [
            {"id": "F1", "name": "one", "status": "done"},
            {"id": "F2", "name": "engine migration", "status": "blocked",
             "blocked_question": "Migrate, or descope?",
             "blocked_recommendation": "Descope for now.",
             "blocked_answer": answer},
            {"id": "F3", "name": "three", "status": "pending"},
        ],
    }


def test_every_session_is_told_how_to_flag_a_decision_it_cannot_make(tmp_path, isolate_tempa_paths):
    """Before this rule existed the only sanctioned "I'm stuck" channel was blocked_by_epic,
    which can only ever point at another epic — so a session facing a genuine fork had nowhere
    to put it and the stall guard eventually failed the whole run."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    prompt = tp.build_session_prompt(_sample_config(tmp_path), "e1")
    assert "A DECISION ONLY THE USER CAN MAKE" in prompt
    assert '"blocked_question"' in prompt


def test_the_decision_rule_rules_out_the_excuses_it_would_otherwise_invite(tmp_path, isolate_tempa_paths):
    """"It is large" / "I am out of budget" / "it looks risky" all describe work, not a fork —
    and an escape hatch that accepts them is one that gets used to avoid implementing."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    prompt = tp.build_session_prompt(_sample_config(tmp_path), "e1")
    assert "It is large" in prompt
    assert "running out of" in prompt
    assert "actually attempted the feature this session" in prompt


def test_a_blocked_feature_is_listed_as_off_limits_not_as_work(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[_blocked_epic()])

    block = tp._build_features_block(config, "e1")

    assert "⛔ F2 — engine migration" in block
    assert "DO NOT work on these" in block
    # The rest of the epic is still ordinary work — one open question doesn't idle the epic.
    assert "⬜ F3 — three" in block


def test_an_answered_feature_is_not_listed_as_off_limits(tmp_path, isolate_tempa_paths):
    config = _sample_config(tmp_path, epic=[_blocked_epic(answer="Descope it.")])
    assert "⛔" not in tp._build_features_block(config, "e1")


def test_the_users_decision_is_handed_to_the_session_that_acts_on_it(tmp_path, isolate_tempa_paths):
    """The answer stays on the feature after the epic is requeued, so the session picking it up
    gets both the question it asked and what came back — instead of re-deriving it from a log."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[_blocked_epic(answer="Descope it, as you suggested.")])

    prompt = tp.build_session_prompt(config, "e1")

    assert "HAS BEEN ANSWERED" in prompt
    assert "Descope it, as you suggested." in prompt
    assert "Migrate, or descope?" in prompt
    # A decision the user has already weighed against the spec must not be re-litigated.
    assert "Do not re-argue it." in prompt


def test_no_answered_decision_block_when_nothing_has_been_answered(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[_blocked_epic()])
    assert "HAS BEEN ANSWERED" not in tp.build_session_prompt(config, "e1")
