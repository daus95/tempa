"""Tests for tempa_prompts.py — prompt template loading and ${...} substitution.

Uses synthetic templates written into the isolated PROMPT_DIR (see conftest.py) rather
than the real src/prompt/*.md files, so template-wording changes don't make these tests
brittle. A separate smoke test at the bottom checks the real shipped templates exist."""

from __future__ import annotations

from pathlib import Path

import pytest

import tempa_backend as tb
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


def test_principles_conflict_instruction_points_somewhere_that_reaches_a_human(isolate_tempa_paths):
    """It used to end "report the conflict explicitly and stop" — which contradicted the
    autonomous system prompt's "FORBIDDEN: … stopping after analysis" and, worse, pointed
    nowhere: a session doing exactly as told wrote the conflict into its closing prose, which
    the runner can only read as a round that completed no feature."""
    path = tempa_config.get_principles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Use dependency injection.", encoding="utf-8")

    block = tp._principles_block()

    assert "RECORD the conflict" in block
    assert "does not reach anyone" in block
    assert "and stop." not in block


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


def test_clarification_prompt_renders_the_severity_scope(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "SCOPE: ${finding_scope}")
    config = _sample_config(tmp_path)
    for scope, text in tp.SEVERITY_SCOPES.items():
        assert text in tp.build_clarification_prompt(config, severity_scope=scope)


def test_an_unknown_severity_scope_falls_back_to_skip_minor(tmp_path, isolate_tempa_paths):
    """Rather than rendering a scope line the agent cannot act on."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "SCOPE: ${finding_scope}")
    config = _sample_config(tmp_path)
    assert tp.SEVERITY_SCOPES["critical_major"] in tp.build_clarification_prompt(
        config, skip_minor=True, severity_scope="nonsense")
    assert tp.SEVERITY_SCOPES["all"] in tp.build_clarification_prompt(
        config, skip_minor=False, severity_scope=None)


def test_clarification_prompt_renders_the_coverage_ledger(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification",
                  "DIR: ${coverage_dir}\n${previous_coverage_ledger}")
    config = _sample_config(tmp_path)
    prompt = tp.build_clarification_prompt(
        config, coverage_dir="/w/coverage",
        previous_ledger=("coverage-20260101-000000.md", "  a table  "))
    assert "DIR: /w/coverage" in prompt
    assert "--- coverage-20260101-000000.md ---" in prompt
    assert "a table" in prompt


def test_no_previous_ledger_says_so_rather_than_leaving_a_dangling_header(
    tmp_path, isolate_tempa_paths,
):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification",
                  "${previous_coverage_ledger}")
    prompt = tp.build_clarification_prompt(_sample_config(tmp_path))
    assert "No previous coverage ledger" in prompt
    assert "${previous_coverage_ledger}" not in prompt


def test_an_oversized_ledger_is_truncated_with_an_instruction(tmp_path, isolate_tempa_paths):
    """Truncating is safe because the prompt has the agent re-derive the inventory anyway: a
    missing tail costs re-derivation, not correctness."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification",
                  "${previous_coverage_ledger}")
    huge = "row\n" * tp.LEDGER_PROMPT_MAX_CHARS
    prompt = tp.build_clarification_prompt(
        _sample_config(tmp_path), previous_ledger=("coverage-20260101-000000.md", huge))
    assert len(prompt) < len(huge)
    assert "this ledger was truncated" in prompt


def test_clarification_prompt_lists_the_findings_to_account_for(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${carried_findings}")
    prompt = tp.build_clarification_prompt(_sample_config(tmp_path), carried_findings=[
        ("C1", "critical", "Void has no screen", True),
        ("M2", "major", "invoice_no has no generator", False),
    ])
    assert "- C1 (critical, answered) — Void has no screen" in prompt
    assert "- M2 (major, unanswered) — invoice_no has no generator" in prompt


def test_nothing_to_carry_says_so_and_asks_for_no_table(tmp_path, isolate_tempa_paths):
    """Rather than leaving the agent to invent an empty table whose markers the parser would
    then read as an account of nothing."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${carried_findings}")
    prompt = tp.build_clarification_prompt(_sample_config(tmp_path))
    assert "nothing to carry over" in prompt
    assert "Omit Part 3's table" in prompt


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
# ${output_language} — the Evaluation card's Language selector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("builder", [
    lambda config: tp.build_clarification_prompt(config),
    lambda config: tp.build_auto_answer_prompt(config, []),
    lambda config: tp.build_apply_clarification_prompt(config, []),
])
@pytest.mark.parametrize("language", [None, "en", "not-a-language"])
def test_english_leaves_the_prompt_exactly_as_it_was(tmp_path, isolate_tempa_paths, builder,
                                                     language):
    """English — the default, and the fallback for an unrecognized code — renders no block at
    all, so every workspace that never touched the picker sends a byte-identical prompt."""
    for name in ("clarification", "auto_answer", "apply_clarification"):
        _write_prompt(isolate_tempa_paths["prompt_dir"], name, "${output_language}BODY")
    overrides = {} if language is None else {"clarification_language": language}
    assert builder(_sample_config(tmp_path, **overrides)) == "BODY"


def test_clarification_prompt_states_the_language_and_what_stays_english(tmp_path,
                                                                        isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${output_language}BODY")
    prompt = tp.build_clarification_prompt(
        _sample_config(tmp_path, clarification_language="id"))
    assert "Indonesian (Bahasa Indonesia)" in prompt
    # The parser matches these literally (dashboard_clarify_parse.LABEL_RE / ITEM_RE), so a
    # translated one drops the finding out of the answer UI entirely.
    for marker in ("**Where:**", "**Question:**", "**Recommendation:**", "**Your answer:**",
                   "clarify:item", "clarify:answer-start", "clarify:enditem"):
        assert marker in prompt
    # Spec references are resolved against the PRD's own wording (dashboard_spec_refs).
    assert "verbatim" in prompt
    assert prompt.endswith("BODY")


def test_auto_answer_prompt_follows_the_same_language(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "auto_answer", "${output_language}BODY")
    prompt = tp.build_auto_answer_prompt(
        _sample_config(tmp_path, clarification_language="ja"), [])
    assert "Japanese (日本語)" in prompt
    assert "**Your answer:**" in prompt


def test_apply_prompt_keeps_the_prd_in_its_own_language(tmp_path, isolate_tempa_paths):
    """Apply is the one clarification stage that writes into the PRD, so its block says the
    opposite of the other two: the answers are translated, the PRD is not."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "apply_clarification",
                  "${output_language}BODY")
    prompt = tp.build_apply_clarification_prompt(
        _sample_config(tmp_path, clarification_language="id"), [])
    assert "Do not translate any part of the PRD" in prompt


def test_every_offered_language_renders_a_block(tmp_path, isolate_tempa_paths):
    """Whatever the dashboard offers must be substitutable — a code with no prompt name would
    reach the agent as a bare code or a KeyError."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "clarification", "${output_language}BODY")
    for code, name, _label in tempa_config.CLARIFICATION_LANGUAGES:
        prompt = tp.build_clarification_prompt(_sample_config(tmp_path,
                                                              clarification_language=code))
        assert ("BODY" if code == "en" else name) in prompt
        assert "${output_language}" not in prompt


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


def test_real_clarification_template_keeps_the_recommendation_checks():
    # A recommendation is normally accepted verbatim ("Follow the recommendation"), so its text
    # becomes the PRD's text. Without these four checks each round's answers add unreviewed
    # surface, and the next round spends itself on holes the previous round's answers opened.
    template = _real_template("clarification")
    assert "=== BEFORE YOU WRITE A RECOMMENDATION DOWN ===" in template
    for check in (
        "ANSWER YOUR OWN QUESTION, AND NOTHING ELSE",
        "THE REASON PARAGRAPH IS SPECIFICATION TOO",
        "COLLISION CHECK",
        "CLOSE WHAT YOU ADD",
    ):
        assert check in template


def test_real_apply_template_narrows_cross_file_supersede():
    # Read widely, "the later file supersedes the earlier one" lets a sweeping clause in one
    # round's reason paragraph silently revoke an earlier round's decision that nothing
    # re-decided — the exact shape that costs a whole extra clarification round to find.
    template = _real_template("apply_clarification")
    assert "wins only on the point it actually decides" in template
    assert "narrowest reading that keeps both decisions true" in template


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


# ---------------------------------------------------------------------------
# _last_round_note_block — what the previous stalled round concluded
# ---------------------------------------------------------------------------

def test_no_note_block_when_the_last_round_moved_the_epic_forward(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[{"epic_name": "e1", "status": "on_progress"}])
    assert "PREVIOUS ROUND" not in tp.build_session_prompt(config, "e1")


def test_the_previous_rounds_conclusion_is_carried_into_the_next_prompt(tmp_path, isolate_tempa_paths):
    """One epic spent four consecutive rounds re-deriving the same conclusion because nothing
    carried it forward — each round ended in a longer restatement than the last."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[{
        "epic_name": "e1", "status": "require_fixing",
        "last_round_note": "m11.workflow.levels has no per-key merge.",
    }])

    prompt = tp.build_session_prompt(config, "e1")

    assert "m11.workflow.levels has no per-key merge." in prompt


def test_the_carried_note_is_framed_as_a_claim_to_check_not_a_finding(tmp_path, isolate_tempa_paths):
    """Handed over as a finding it entrenches whatever the last round believed — and on the epic
    this was built for, the first three rounds' blockers were disproved by the fourth."""
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[{
        "epic_name": "e1", "status": "require_fixing", "last_round_note": "blocked on X",
    }])

    prompt = tp.build_session_prompt(config, "e1")

    assert "CLAIM TO CHECK" in prompt
    assert "has turned out to be wrong before" in prompt
    # Both acceptable outcomes named, so "re-derive and restate it again" isn't one of them.
    assert "does NOT hold" in prompt and "blocked-feature rule" in prompt


def test_a_blank_note_is_not_carried(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "implementation", "IMPL")
    config = _sample_config(tmp_path, epic=[{
        "epic_name": "e1", "status": "require_fixing", "last_round_note": "   \n ",
    }])
    assert "PREVIOUS ROUND" not in tp.build_session_prompt(config, "e1")


# ---------------------------------------------------------------------------
# _collectable_work_block — the turn that ends to wait
#
# AUTONOMOUS_SYSTEM_PROMPT has forbidden ending a turn to wait since 0.6.6, and the EPIC-02
# incident happened anyway in a session that COMPLIED with it: it asked for the foreground with
# an explicit 600000ms timeout and the harness answered "moved to the background". A prohibition
# the harness converts into the forbidden action is not an instruction, which is why this block
# names a reachable move instead of repeating the ban.
# ---------------------------------------------------------------------------

def _prompt_config(**epic_overrides):
    epic = {"epic_name": "EPIC-02", "status": "on_progress", "completed_features": 2,
            "total_features": 6, "features": []}
    epic.update(epic_overrides)
    return {"epic": [epic], "features_per_session": 3}


def test_the_implementation_prompt_forbids_ending_a_turn_to_wait():
    prompt = tp.build_session_prompt(_prompt_config(), "EPIC-02")

    assert "DO NOT START WORK THIS SESSION CANNOT COLLECT" in prompt
    assert "ends the instant you reply without calling a tool" in prompt
    # The quoted sentence is wrapped across a line in the block, so match its two halves rather
    # than a span that only exists once the wrapping is undone.
    assert "I'll report back once it" in prompt
    assert "is the one closing sentence that guarantees nothing ever does" in prompt


def test_the_prompt_says_to_narrow_a_run_rather_than_background_or_extend_it():
    """The incident session asked for its tool's ceiling and was auto-backgrounded anyway, then
    had its compliant foreground wait backgrounded too. "Use the foreground" is not a followable
    instruction for a twelve-minute suite; "narrow it until it fits" is."""
    prompt = tp.build_session_prompt(_prompt_config(), "EPIC-02")

    assert "NARROW the run until it fits" in prompt
    assert "NOT ask for a longer timeout" in prompt


def test_no_prompt_surface_tells_the_agent_to_raise_a_timeout():
    """Shipping a session prompt that says "narrow it" alongside a system prompt that says
    "raise the timeout" would be two prompts contradicting each other."""
    surfaces = [
        tp.build_session_prompt(_prompt_config(), "EPIC-02"),
        tp.build_qa_prompt(_prompt_config(), "EPIC-02", Path("qa.md")),
        tb.AUTONOMOUS_SYSTEM_PROMPT,
    ]
    for surface in surfaces:
        assert "raised timeout" not in surface
        assert "raise the timeout" not in surface


def test_the_prompt_puts_the_config_record_before_the_verification_run():
    """The ordering inversion is the clause that actually breaks the incident: session #1
    finished its entire allotted batch of three features, verified all of it, and recorded none
    of it because it was waiting to run a wider regression suite first."""
    prompt = tp.build_session_prompt(_prompt_config(), "EPIC-02")

    assert "BEFORE you start any wider regression or verification run" in prompt
    assert prompt.index("BEFORE you start any wider regression") > prompt.index("MANDATORY RULE — DO NOT SKIP")


def test_a_feature_that_cannot_be_checked_is_pointed_at_the_two_rules_below():
    """The trade this rule accepts is that a feature is recorded before a wider run has had its
    say. It must not become licence to record a feature that was never checked at all."""
    prompt = tp.build_session_prompt(_prompt_config(), "EPIC-02")

    assert "check at all, stays as it is" in prompt
    assert "rather than your closing message" in prompt


def test_the_qa_prompt_carries_the_rule_and_names_its_own_record():
    """qa.md tells the session to start the application and never tells it to stop it, so QA is
    the session shape most likely to leave work running."""
    prompt = tp.build_qa_prompt(_prompt_config(), "EPIC-02", Path("qa.md"))

    assert "DO NOT START WORK THIS SESSION CANNOT COLLECT" in prompt
    assert "qa_status" in prompt
    assert "the loop guard" in prompt
    assert "regression or verification run" not in prompt


def test_the_collect_rule_survives_a_user_emptying_the_templates():
    """Pins the "Python, not a template placeholder" decision: every rule the runner's own state
    machine depends on lives in this module, so a user tuning stage wording cannot delete it."""
    (tempa_config.PROMPT_DIR).mkdir(parents=True, exist_ok=True)
    (tempa_config.PROMPT_DIR / "implementation.md").write_text("", encoding="utf-8")
    (tempa_config.PROMPT_DIR / "qa.md").write_text("", encoding="utf-8")

    assert "DO NOT START WORK THIS SESSION CANNOT COLLECT" in tp.build_session_prompt(
        _prompt_config(), "EPIC-02")
    assert "DO NOT START WORK THIS SESSION CANNOT COLLECT" in tp.build_qa_prompt(
        _prompt_config(), "EPIC-02", Path("qa.md"))


def test_an_unfinished_check_is_not_handed_over_as_a_claim_to_check():
    """Round 2 of the incident was handed round 1's "I'll report back once it completes." under
    the CLAIM TO CHECK header, followed by "Check it against the code as it stands now, first".
    It did: it re-ran the suite, in the background, and ended the same way."""
    config = _prompt_config(
        last_round_note="I'm waiting for the full failure-list test run to complete.",
        last_round_note_kind="unfinished_check",
    )
    prompt = tp.build_session_prompt(config, "EPIC-02")

    assert "AN UNFINISHED CHECK FROM THE ROUND BEFORE" in prompt
    assert "CLAIM TO CHECK" not in prompt
    assert "foreground command" in prompt


def test_a_note_with_no_kind_still_warns_about_a_round_that_was_only_waiting():
    """Closes the legacy exposure: every note written before last_round_note_kind existed carries
    no kind, including the one sitting on EPIC-02 right now. A real blocker must still arrive as a
    claim, so the warning is merged into the default frame rather than replacing it."""
    config = _prompt_config(
        last_round_note=(
            "I'm waiting for the full failure-list test run (background) to complete so I can "
            "confirm whether the 16th failure is a genuine regression. I'll report back once it "
            "finishes."
        ),
    )
    prompt = tp.build_session_prompt(config, "EPIC-02")

    assert "CLAIM TO CHECK" in prompt
    assert "neither a claim nor a blocker" in prompt
