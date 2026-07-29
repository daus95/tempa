"""Tests for tempa_prompts.py — prompt template loading and ${...} substitution.

Uses synthetic templates written into the isolated PROMPT_DIR (see conftest.py) rather
than the real src/prompt/*.md files, so template-wording changes don't make these tests
brittle. A separate smoke test at the bottom checks the real shipped templates exist."""

from __future__ import annotations

from pathlib import Path

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
    _write_prompt(isolate_tempa_paths["prompt_dir"], "apply_clarification", "APPLY ${sources.prd}")
    config = _sample_config(tmp_path)
    prompt = tp.build_apply_clarification_prompt(config)
    assert "APPLY " in prompt
    assert "${sources.prd}" not in prompt


def test_build_auto_answer_prompt(tmp_path, isolate_tempa_paths):
    _write_prompt(isolate_tempa_paths["prompt_dir"], "auto_answer", "AUTO ${sources.clarifications}")
    config = _sample_config(tmp_path)
    prompt = tp.build_auto_answer_prompt(config)
    assert "AUTO " in prompt
    assert "${sources.clarifications}" not in prompt


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
