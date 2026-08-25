"""Tests for tempa_config.py — config load/save, workspace/sources/model resolution,
and the active-workspace pointer. All isolated from the real dev machine via the
autouse `isolate_tempa_paths` fixture in conftest.py."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

import tempa_config

# ---------------------------------------------------------------------------
# load_config / save_config / read_config_safe
# ---------------------------------------------------------------------------

def test_load_config_no_file_no_workspace_returns_default(isolate_tempa_paths):
    config = tempa_config.load_config()
    assert config == tempa_config.DEFAULT_CONFIG
    assert not tempa_config.get_config_path().exists()


def test_load_config_no_file_active_workspace_persists_default(tmp_path, isolate_tempa_paths):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    tempa_config.set_active_workspace_root(workspace_root)

    config = tempa_config.load_config()

    assert config == tempa_config.DEFAULT_CONFIG
    assert tempa_config.get_config_path().exists()
    on_disk = json.loads(tempa_config.get_config_path().read_text(encoding="utf-8"))
    assert on_disk == tempa_config.DEFAULT_CONFIG


def test_load_config_existing_file_returned_verbatim(isolate_tempa_paths):
    custom = {"models": {"clarify": "custom-model"}, "epic": [{"epic_name": "x"}]}
    tempa_config.save_config(custom)

    assert tempa_config.load_config() == custom


def test_save_config_creates_parent_dirs(isolate_tempa_paths):
    assert not tempa_config.get_config_path().parent.exists()
    tempa_config.save_config({"a": 1})
    assert tempa_config.get_config_path().exists()
    assert json.loads(tempa_config.get_config_path().read_text(encoding="utf-8")) == {"a": 1}


def test_save_config_no_leftover_temp_file(isolate_tempa_paths):
    tempa_config.save_config({"a": 1})
    siblings = list(tempa_config.get_config_path().parent.iterdir())
    assert siblings == [tempa_config.get_config_path()]


def test_save_config_overwrites_existing_file_atomically(isolate_tempa_paths):
    tempa_config.save_config({"a": 1})
    tempa_config.save_config({"a": 2, "b": 3})
    assert json.loads(tempa_config.get_config_path().read_text(encoding="utf-8")) == {"a": 2, "b": 3}


def test_read_config_safe_missing_file_returns_empty_dict(isolate_tempa_paths):
    assert tempa_config.read_config_safe() == {}


def test_read_config_safe_invalid_json_returns_empty_dict(isolate_tempa_paths):
    path = tempa_config.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert tempa_config.read_config_safe() == {}


def test_read_config_safe_non_dict_json_returns_empty_dict(isolate_tempa_paths):
    path = tempa_config.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert tempa_config.read_config_safe() == {}


def test_read_config_safe_valid_dict_returned_unchanged(isolate_tempa_paths):
    tempa_config.save_config({"foo": "bar"})
    assert tempa_config.read_config_safe() == {"foo": "bar"}


# ---------------------------------------------------------------------------
# get_workspace
# ---------------------------------------------------------------------------

def test_get_workspace_no_key_returns_default():
    assert tempa_config.get_workspace({}) == tempa_config.DEFAULT_WORKSPACE


def test_get_workspace_partial_override_merges_defaults():
    workspace = tempa_config.get_workspace({"workspace": {"root": "/some/root"}})
    assert workspace["root"] == "/some/root"
    assert workspace["docs"] == tempa_config.DEFAULT_WORKSPACE["docs"]
    assert workspace["apps"] == tempa_config.DEFAULT_WORKSPACE["apps"]


# ---------------------------------------------------------------------------
# resolve_workspace_paths
# ---------------------------------------------------------------------------

def test_resolve_workspace_paths_empty_root_returns_empty_dict():
    assert tempa_config.resolve_workspace_paths({}) == {}
    assert tempa_config.resolve_workspace_paths({"workspace": {"root": ""}}) == {}


def test_resolve_workspace_paths_joins_onto_root(tmp_path):
    root = tmp_path / "myproject"
    config = {"workspace": {"root": str(root)}}
    resolved = tempa_config.resolve_workspace_paths(config)

    assert resolved["root"] == str(root)
    assert resolved["docs"] == str(root / "docs")
    assert resolved["apps"] == str(root / "src")
    assert resolved["infra"] == str(root / "infra")
    assert resolved["archive"] == str(root / "archive")
    # specs is the one asymmetric key: nested under root/.tempa/<specs-rel>
    assert resolved["specs"] == str(root / ".tempa" / "specs")


# ---------------------------------------------------------------------------
# resolve_source_path
# ---------------------------------------------------------------------------

def test_resolve_source_path_empty_value_returned_unchanged():
    assert tempa_config.resolve_source_path({}, "") == ""


def test_resolve_source_path_absolute_value_returned_as_is(tmp_path):
    abs_path = str(tmp_path / "abs" / "dir")
    config = {"workspace": {"root": str(tmp_path / "other_root")}}
    assert tempa_config.resolve_source_path(config, abs_path) == abs_path


def test_resolve_source_path_relative_with_root_joined(tmp_path):
    root = tmp_path / "root"
    config = {"workspace": {"root": str(root)}}
    assert tempa_config.resolve_source_path(config, "sub/dir") == str(root / "sub/dir")


def test_resolve_source_path_relative_without_root_returned_unchanged():
    config = {"workspace": {"root": ""}}
    assert tempa_config.resolve_source_path(config, "sub/dir") == "sub/dir"


# ---------------------------------------------------------------------------
# get_sources
# ---------------------------------------------------------------------------

def test_get_sources_defaults_derived_from_workspace(tmp_path):
    root = tmp_path / "root"
    config = {"workspace": {"root": str(root)}}
    sources = tempa_config.get_sources(config)

    specs_dir = tempa_config.resolve_specs_dir(config)
    assert sources["docs"] == str(root / "docs")
    assert sources["apps"] == str(root / "src")
    assert sources["prd"] == str(specs_dir / "prd")
    assert sources["epics"] == str(specs_dir / "pbi/epics")
    assert sources["clarifications"] == str(specs_dir / "clarifications")


def test_get_sources_explicit_override_resolved(tmp_path):
    root = tmp_path / "root"
    config = {"workspace": {"root": str(root)}, "sources": {"prd": "custom/prd"}}
    sources = tempa_config.get_sources(config)
    assert sources["prd"] == str(root / "custom/prd")


def test_get_sources_falsy_override_falls_back_to_default(tmp_path):
    root = tmp_path / "root"
    config = {"workspace": {"root": str(root)}, "sources": {"epics": ""}}
    sources = tempa_config.get_sources(config)
    specs_dir = tempa_config.resolve_specs_dir(config)
    assert sources["epics"] == str(specs_dir / "pbi/epics")


# ---------------------------------------------------------------------------
# resolve_specs_dir
# ---------------------------------------------------------------------------

def test_resolve_specs_dir_root_configured(tmp_path):
    root = tmp_path / "root"
    config = {"workspace": {"root": str(root), "specs": "myspecs"}}
    assert tempa_config.resolve_specs_dir(config) == root / ".tempa" / "myspecs"


def test_resolve_specs_dir_no_root_relative_uses_working_dir(isolate_tempa_paths):
    config = {"workspace": {"specs": "myspecs"}}
    assert tempa_config.resolve_specs_dir(config) == tempa_config.WORKING_DIR / "myspecs"


def test_resolve_specs_dir_no_root_absolute_returned_as_is(tmp_path):
    abs_specs = tmp_path / "abs_specs"
    config = {"workspace": {"specs": str(abs_specs)}}
    assert tempa_config.resolve_specs_dir(config) == abs_specs


def test_resolve_specs_dir_missing_specs_key_falls_back_to_literal(isolate_tempa_paths):
    config = {"workspace": {}}
    assert tempa_config.resolve_specs_dir(config) == tempa_config.WORKING_DIR / "specs"


# ---------------------------------------------------------------------------
# resolve_prd_dir / resolve_clar_dir
# ---------------------------------------------------------------------------

def test_resolve_prd_dir(tmp_path):
    config = {"workspace": {"root": str(tmp_path)}}
    from pathlib import Path
    assert tempa_config.resolve_prd_dir(config) == Path(tempa_config.get_sources(config)["prd"])


def test_resolve_clar_dir(tmp_path):
    config = {"workspace": {"root": str(tmp_path)}}
    from pathlib import Path
    assert tempa_config.resolve_clar_dir(config) == Path(tempa_config.get_sources(config)["clarifications"])


# ---------------------------------------------------------------------------
# _resolve_model_alias
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alias,expected", [
    ("opus-5", "claude-opus-5"),
    ("opus", "claude-opus-5"),
    ("sonnet-5", "claude-sonnet-5"),
    ("sonnet", "claude-sonnet-5"),
    ("haiku-4.5", "claude-haiku-4-5-20251001"),
    ("haiku", "claude-haiku-4-5-20251001"),
    ("fable-5", "claude-fable-5"),
    ("fable", "claude-fable-5"),
])
def test_resolve_model_alias_known(alias, expected):
    assert tempa_config._resolve_model_alias(alias) == expected


def test_resolve_model_alias_whitespace_and_case_insensitive():
    assert tempa_config._resolve_model_alias(" Opus-5 ") == "claude-opus-5"


def test_resolve_model_alias_unknown_returned_unchanged():
    assert tempa_config._resolve_model_alias("some-custom-model-id") == "some-custom-model-id"


# ---------------------------------------------------------------------------
# get_models / get_model
# ---------------------------------------------------------------------------

def test_get_models_empty_config_returns_defaults():
    assert tempa_config.get_models({}) == tempa_config.DEFAULT_MODELS


def test_get_models_partial_override_merges():
    models = tempa_config.get_models({"models": {"implement": "custom"}})
    assert models["implement"] == "custom"
    assert models["clarify"] == tempa_config.DEFAULT_MODELS["clarify"]
    assert models["plan"] == tempa_config.DEFAULT_MODELS["plan"]


def test_get_model_stage_present():
    config = {"models": {"implement": "custom-model"}}
    assert tempa_config.get_model(config, "implement") == "custom-model"


def test_get_model_stage_absent_from_models_dict_falls_back_to_default():
    config = {"models": {}}
    assert tempa_config.get_model(config, "clarify") == tempa_config.DEFAULT_MODELS["clarify"]


def test_get_model_unknown_stage_falls_back_to_hardcoded_default():
    assert tempa_config.get_model({}, "nonexistent-stage") == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# get_backends / get_backend
# ---------------------------------------------------------------------------

def test_get_backends_empty_config_returns_defaults():
    assert tempa_config.get_backends({}) == tempa_config.DEFAULT_BACKENDS


def test_get_backends_partial_override_merges():
    backends = tempa_config.get_backends({"backends": {"implement": "codex"}})
    assert backends["implement"] == "codex"
    assert backends["clarify"] == tempa_config.DEFAULT_BACKENDS["clarify"]
    assert backends["plan"] == tempa_config.DEFAULT_BACKENDS["plan"]


def test_get_backend_stage_present():
    config = {"backends": {"implement": "copilot"}}
    assert tempa_config.get_backend(config, "implement") == "copilot"


def test_get_backend_stage_absent_falls_back_to_claude():
    assert tempa_config.get_backend({"backends": {}}, "clarify") == "claude"


def test_get_backend_unknown_stage_falls_back_to_claude():
    assert tempa_config.get_backend({}, "nonexistent-stage") == "claude"


def test_get_backend_clarify_apply_is_independent_stage():
    # clarify_apply is a full stage — its backend can differ from clarify's, not just
    # follow it (see DEFAULT_BACKENDS in tempa_config.py).
    assert tempa_config.get_backend({}, "clarify_apply") == "claude"
    config = {"backends": {"clarify": "claude", "clarify_apply": "codex"}}
    assert tempa_config.get_backend(config, "clarify") == "claude"
    assert tempa_config.get_backend(config, "clarify_apply") == "codex"


# ---------------------------------------------------------------------------
# get_reasoning_efforts / get_reasoning_effort
# ---------------------------------------------------------------------------

def test_get_reasoning_efforts_empty_config_returns_defaults():
    assert tempa_config.get_reasoning_efforts({}) == tempa_config.DEFAULT_REASONING_EFFORTS


def test_get_reasoning_efforts_partial_override_merges():
    efforts = tempa_config.get_reasoning_efforts({"reasoning_efforts": {"implement": "high"}})
    assert efforts["implement"] == "high"
    assert efforts["clarify"] == tempa_config.DEFAULT_REASONING_EFFORTS["clarify"]
    assert efforts["plan"] == tempa_config.DEFAULT_REASONING_EFFORTS["plan"]


def test_get_reasoning_effort_stage_present():
    config = {"reasoning_efforts": {"implement": "xhigh"}}
    assert tempa_config.get_reasoning_effort(config, "implement") == "xhigh"


def test_get_reasoning_effort_stage_absent_falls_back_to_empty():
    assert tempa_config.get_reasoning_effort({"reasoning_efforts": {}}, "clarify") == ""


def test_get_reasoning_effort_unknown_stage_falls_back_to_empty():
    assert tempa_config.get_reasoning_effort({}, "nonexistent-stage") == ""


def test_get_reasoning_effort_clarify_apply_is_independent_stage():
    assert tempa_config.get_reasoning_effort({}, "clarify_apply") == ""
    config = {"reasoning_efforts": {"clarify": "high", "clarify_apply": "low"}}
    assert tempa_config.get_reasoning_effort(config, "clarify") == "high"
    assert tempa_config.get_reasoning_effort(config, "clarify_apply") == "low"


# ---------------------------------------------------------------------------
# get_epic_session_id / set_epic_session_id
# ---------------------------------------------------------------------------

def test_get_epic_session_id_absent_returns_none():
    assert tempa_config.get_epic_session_id({}, "claude", kind="implement") is None
    assert tempa_config.get_epic_session_id({}, "claude", kind="qa") is None


def test_set_epic_session_id_then_get_round_trips_for_matching_backend():
    epic = {}
    tempa_config.set_epic_session_id(epic, "codex", "thread-123", kind="implement")
    assert tempa_config.get_epic_session_id(epic, "codex", kind="implement") == "thread-123"


def test_get_epic_session_id_returns_none_when_backend_changed_since_capture():
    epic = {}
    tempa_config.set_epic_session_id(epic, "codex", "thread-123", kind="implement")
    # The stage's backend was switched to claude after this id was captured under codex —
    # resuming with it would be meaningless, so it must not be returned.
    assert tempa_config.get_epic_session_id(epic, "claude", kind="implement") is None


def test_get_epic_session_id_qa_kind_is_independent_of_implement_kind():
    epic = {}
    tempa_config.set_epic_session_id(epic, "claude", "impl-sid", kind="implement")
    tempa_config.set_epic_session_id(epic, "codex", "qa-sid", kind="qa")
    assert tempa_config.get_epic_session_id(epic, "claude", kind="implement") == "impl-sid"
    assert tempa_config.get_epic_session_id(epic, "codex", kind="qa") == "qa-sid"


def test_get_epic_session_id_legacy_bare_claude_session_id_treated_as_claude_backend():
    # Configs written before multi-backend support only had "claude_session_id", no
    # "session_backend" companion field — must still resolve for backend "claude" (the
    # only backend that existed when they were written), and not for anything else.
    epic = {"claude_session_id": "legacy-sid"}
    assert tempa_config.get_epic_session_id(epic, "claude", kind="implement") == "legacy-sid"
    assert tempa_config.get_epic_session_id(epic, "codex", kind="implement") is None


def test_set_epic_session_id_drops_legacy_claude_session_id_key():
    epic = {"claude_session_id": "legacy-sid"}
    tempa_config.set_epic_session_id(epic, "codex", "new-sid", kind="implement")
    assert "claude_session_id" not in epic
    assert epic["session_id"] == "new-sid"
    assert epic["session_backend"] == "codex"


def test_get_epic_session_id_qa_has_no_legacy_key_fallback():
    # "qa_session_id" was already backend-agnostic-looking before multi-backend support
    # (no separate legacy key like implement's claude_session_id) — an old bare value with
    # no qa_session_backend companion is still treated as backend "claude".
    epic = {"qa_session_id": "old-qa-sid"}
    assert tempa_config.get_epic_session_id(epic, "claude", kind="qa") == "old-qa-sid"
    assert tempa_config.get_epic_session_id(epic, "codex", kind="qa") is None


# ---------------------------------------------------------------------------
# get_clarify_session_id / get_clarify_apply_session_id
# ---------------------------------------------------------------------------

def test_get_clarify_session_id_absent_returns_none():
    assert tempa_config.get_clarify_session_id({}, "claude") is None


def test_get_clarify_session_id_returns_id_for_matching_backend():
    config = {"clarify_session_id": "eval-sid", "clarify_session_backend": "claude"}
    assert tempa_config.get_clarify_session_id(config, "claude") == "eval-sid"


def test_get_clarify_session_id_none_on_backend_mismatch():
    config = {"clarify_session_id": "eval-sid", "clarify_session_backend": "codex"}
    assert tempa_config.get_clarify_session_id(config, "claude") is None


def test_get_clarify_apply_session_id_independent_of_evaluate_session_id():
    config = {
        "clarify_session_id": "eval-sid", "clarify_session_backend": "claude",
        "clarify_apply_session_id": "apply-sid", "clarify_apply_session_backend": "claude",
    }
    assert tempa_config.get_clarify_session_id(config, "claude") == "eval-sid"
    assert tempa_config.get_clarify_apply_session_id(config, "claude") == "apply-sid"


# ---------------------------------------------------------------------------
# get_resume_implementation_sessions
# ---------------------------------------------------------------------------

def test_get_resume_implementation_sessions_defaults_true():
    assert tempa_config.get_resume_implementation_sessions({}) is True


def test_get_resume_implementation_sessions_respects_false():
    assert tempa_config.get_resume_implementation_sessions({"resume_implementation_sessions": False}) is False


def test_get_resume_implementation_sessions_ignores_non_bool_value():
    assert tempa_config.get_resume_implementation_sessions({"resume_implementation_sessions": "nope"}) is True


# ---------------------------------------------------------------------------
# get_commit_after_qa_pass
# ---------------------------------------------------------------------------

def test_get_commit_after_qa_pass_defaults_true():
    assert tempa_config.get_commit_after_qa_pass({}) is True


def test_get_commit_after_qa_pass_respects_false():
    assert tempa_config.get_commit_after_qa_pass({"commit_after_qa_pass": False}) is False


def test_get_commit_after_qa_pass_ignores_non_bool_value():
    assert tempa_config.get_commit_after_qa_pass({"commit_after_qa_pass": "nope"}) is True


# ---------------------------------------------------------------------------
# finalize checkpoint settings
# ---------------------------------------------------------------------------

def test_get_finalize_checkpoint_rounds_defaults_to_three():
    assert tempa_config.get_finalize_checkpoint_rounds({}) == 3


def test_get_finalize_checkpoint_rounds_explicit_null_means_disabled():
    """Unlike a missing key (a config written before this setting existed), an explicit null
    is the Settings form's blank field and has to mean "never checkpoint"."""
    assert tempa_config.get_finalize_checkpoint_rounds({"finalize_checkpoint_rounds": None}) is None


def test_get_finalize_checkpoint_rounds_respects_a_positive_value():
    assert tempa_config.get_finalize_checkpoint_rounds({"finalize_checkpoint_rounds": 4}) == 4


@pytest.mark.parametrize("value", [0, -1, "3", 2.5, True])
def test_get_finalize_checkpoint_rounds_falls_back_on_junk(value):
    assert tempa_config.get_finalize_checkpoint_rounds({"finalize_checkpoint_rounds": value}) == 3


def test_get_finalize_checkpoint_commit_defaults_true():
    assert tempa_config.get_finalize_checkpoint_commit({}) is True


def test_get_finalize_checkpoint_commit_respects_false():
    assert tempa_config.get_finalize_checkpoint_commit({"finalize_checkpoint_commit": False}) is False


def test_get_finalize_checkpoint_commit_ignores_non_bool_value():
    assert tempa_config.get_finalize_checkpoint_commit({"finalize_checkpoint_commit": "nope"}) is True


# ---------------------------------------------------------------------------
# get_terminate_leftover_processes
# ---------------------------------------------------------------------------

def test_get_terminate_leftover_processes_defaults_true():
    assert tempa_config.get_terminate_leftover_processes({}) is True


def test_get_terminate_leftover_processes_respects_false():
    assert tempa_config.get_terminate_leftover_processes({"terminate_leftover_processes": False}) is False


def test_get_terminate_leftover_processes_ignores_non_bool_value():
    assert tempa_config.get_terminate_leftover_processes({"terminate_leftover_processes": "nope"}) is True


# ---------------------------------------------------------------------------
# get_model — DEFAULT_MODELS now includes "clarify_apply"
# ---------------------------------------------------------------------------

def test_get_model_clarify_apply_defaults_to_sonnet():
    assert tempa_config.get_model({}, "clarify_apply") == "claude-sonnet-5"


def test_get_model_clarify_apply_overridable():
    config = {"models": {"clarify_apply": "claude-haiku-4-5-20251001"}}
    assert tempa_config.get_model(config, "clarify_apply") == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# active-workspace pointer
# ---------------------------------------------------------------------------

def test_get_active_workspace_root_absent_returns_none(isolate_tempa_paths):
    assert tempa_config.get_active_workspace_root() is None


def test_get_active_workspace_root_blank_pointer_returns_none(isolate_tempa_paths):
    tempa_config.ACTIVE_WORKSPACE_POINTER.write_text("   \n", encoding="utf-8")
    assert tempa_config.get_active_workspace_root() is None


def test_get_active_workspace_root_real_path(tmp_path, isolate_tempa_paths):
    from pathlib import Path
    target = tmp_path / "some_workspace"
    tempa_config.ACTIVE_WORKSPACE_POINTER.write_text(str(target), encoding="utf-8")
    assert tempa_config.get_active_workspace_root() == Path(str(target))


def test_set_active_workspace_root_round_trips(tmp_path, isolate_tempa_paths):
    target = tmp_path / "some_workspace"
    tempa_config.set_active_workspace_root(target)
    assert tempa_config.get_active_workspace_root() == target


def test_clear_active_workspace_root_removes_pointer(tmp_path, isolate_tempa_paths):
    tempa_config.set_active_workspace_root(tmp_path / "w")
    tempa_config.clear_active_workspace_root()
    assert not tempa_config.ACTIVE_WORKSPACE_POINTER.exists()
    assert tempa_config.get_active_workspace_root() is None


def test_clear_active_workspace_root_idempotent(isolate_tempa_paths):
    # Absent already — must not raise.
    tempa_config.clear_active_workspace_root()


# ---------------------------------------------------------------------------
# read_principles
# ---------------------------------------------------------------------------

def test_read_principles_absent_returns_empty_string(isolate_tempa_paths):
    assert tempa_config.read_principles() == ""


def test_read_principles_strips_whitespace(isolate_tempa_paths):
    path = tempa_config.get_principles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("  \n  Some principles.  \n\n", encoding="utf-8")
    assert tempa_config.read_principles() == "Some principles."


def test_read_principles_undecodable_returns_empty_string(isolate_tempa_paths):
    path = tempa_config.get_principles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Invalid UTF-8 byte sequence.
    path.write_bytes(b"\xff\xfe\x00\x00invalid")
    assert tempa_config.read_principles() == ""


# ---------------------------------------------------------------------------
# path getters (_tempa_dir-derived), parametrized over workspace active/inactive
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workspace_active", [False, True])
def test_path_getters_resolve_under_tempa_dir(tmp_path, isolate_tempa_paths, workspace_active):
    if workspace_active:
        root = tmp_path / "workspace"
        root.mkdir()
        tempa_config.set_active_workspace_root(root)
        base = root
    else:
        base = isolate_tempa_paths["script_dir"]

    assert tempa_config.get_config_path() == base / ".tempa" / "config.json"
    assert tempa_config.get_logs_dir() == base / ".tempa" / "logs"
    assert tempa_config.get_qa_dir() == base / ".tempa" / "qa"
    assert tempa_config.get_verify_dir() == base / ".tempa" / "verify"
    assert tempa_config.get_principles_path() == base / ".tempa" / "architecture-principles.md"


# ---------------------------------------------------------------------------
# workspace_is_writable
# ---------------------------------------------------------------------------

def test_workspace_is_writable_empty_root_returns_false():
    assert tempa_config.workspace_is_writable("") is False


def test_workspace_is_writable_nonexistent_root_returns_false(tmp_path):
    assert tempa_config.workspace_is_writable(str(tmp_path / "does-not-exist")) is False


def test_workspace_is_writable_real_writable_dir_returns_true(tmp_path):
    assert tempa_config.workspace_is_writable(str(tmp_path)) is True


def test_workspace_is_writable_leaves_no_probe_file_behind(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    tempa_config.workspace_is_writable(str(root))
    assert list(root.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="chmod-based read-only dirs aren't reliable on Windows")
def test_workspace_is_writable_read_only_dir_returns_false(tmp_path):
    root = tmp_path / "readonly"
    root.mkdir()
    root.chmod(0o500)
    try:
        assert tempa_config.workspace_is_writable(str(root)) is False
    finally:
        root.chmod(0o700)  # restore so tmp_path cleanup can remove it


# ---------------------------------------------------------------------------
# get_clarify_overlay_warn_findings — when the dashboard suggests compacting the
# pending-resolution overlay into the PRD (warning only, never auto-applied).
# ---------------------------------------------------------------------------

def test_clarify_overlay_warn_findings_defaults_when_absent():
    default = tempa_config.DEFAULT_CONFIG["clarify_overlay_warn_findings"]
    assert tempa_config.get_clarify_overlay_warn_findings({}) == default


def test_clarify_overlay_warn_findings_honors_a_positive_int():
    assert tempa_config.get_clarify_overlay_warn_findings({"clarify_overlay_warn_findings": 4}) == 4


@pytest.mark.parametrize("value", [0, -1, "x", True, None])
def test_clarify_overlay_warn_findings_rejects_non_positive_and_junk(value):
    # True is an int in Python but is never a meaningful threshold — _get_positive_number
    # excludes bools explicitly.
    default = tempa_config.DEFAULT_CONFIG["clarify_overlay_warn_findings"]
    assert tempa_config.get_clarify_overlay_warn_findings(
        {"clarify_overlay_warn_findings": value}) == default


# ---------------------------------------------------------------------------
# get_backend_background_wait_sec — how long a backend CLI waits for the background
# work a session left running before killing it and exiting.
# ---------------------------------------------------------------------------

def test_backend_background_wait_sec_defaults_when_absent():
    default = tempa_config.DEFAULT_CONFIG["backend_background_wait_sec"]
    assert tempa_config.get_backend_background_wait_sec({}) == default


def test_backend_background_wait_sec_honors_a_positive_value():
    assert tempa_config.get_backend_background_wait_sec({"backend_background_wait_sec": 1800}) == 1800


def test_backend_background_wait_sec_accepts_zero_as_wait_indefinitely():
    # Unlike every other *_sec setting, 0 is meaningful here rather than invalid: it's the
    # backend CLI's own documented "never give up on background work" value.
    assert tempa_config.get_backend_background_wait_sec({"backend_background_wait_sec": 0}) == 0


@pytest.mark.parametrize("value", [-1, "x", True, None])
def test_backend_background_wait_sec_rejects_negative_and_junk(value):
    default = tempa_config.DEFAULT_CONFIG["backend_background_wait_sec"]
    assert tempa_config.get_backend_background_wait_sec(
        {"backend_background_wait_sec": value}) == default


# ---------------------------------------------------------------------------
# Graceful-stop sentinel — the one cross-process channel between the dashboard
# (or a second terminal) and a running implement/clarify process.
# ---------------------------------------------------------------------------

def test_graceful_stop_not_requested_by_default(isolate_tempa_paths):
    assert tempa_config.graceful_stop_requested("implement") is False
    assert tempa_config.graceful_stop_requested("clarify") is False


def test_graceful_stop_request_then_clear_round_trip(isolate_tempa_paths):
    tempa_config.request_graceful_stop("implement")
    assert tempa_config.graceful_stop_requested("implement") is True
    assert tempa_config.get_graceful_stop_path("implement").exists()

    tempa_config.clear_graceful_stop("implement")
    assert tempa_config.graceful_stop_requested("implement") is False
    assert not tempa_config.get_graceful_stop_path("implement").exists()


def test_graceful_stop_kinds_are_independent(isolate_tempa_paths):
    # Stopping a clarification must never stop an implement run happening alongside it.
    tempa_config.request_graceful_stop("clarify")
    assert tempa_config.graceful_stop_requested("clarify") is True
    assert tempa_config.graceful_stop_requested("implement") is False


def test_clear_graceful_stop_is_a_noop_when_nothing_is_pending(isolate_tempa_paths):
    # Called unconditionally at the start of every run, so "no request" must not raise.
    tempa_config.clear_graceful_stop("implement")
    assert tempa_config.graceful_stop_requested("implement") is False


def test_graceful_stop_path_follows_the_active_workspace(tmp_path, isolate_tempa_paths):
    # The dashboard and the CLI are separate processes; they only ever agree on this file
    # because both resolve it through the active workspace pointer.
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    tempa_config.set_active_workspace_root(workspace_root)

    tempa_config.request_graceful_stop("implement")

    assert tempa_config.get_graceful_stop_path("implement").parent == workspace_root / ".tempa"
    assert (workspace_root / ".tempa" / "graceful-stop-implement").exists()


def test_request_graceful_stop_creates_the_tempa_dir_if_absent(tmp_path, isolate_tempa_paths):
    # A stop can be requested before anything else has written to .tempa/ in this workspace.
    workspace_root = tmp_path / "fresh"
    workspace_root.mkdir()
    tempa_config.set_active_workspace_root(workspace_root)
    assert not (workspace_root / ".tempa").exists()

    tempa_config.request_graceful_stop("clarify")

    assert tempa_config.graceful_stop_requested("clarify") is True


# ---------------------------------------------------------------------------
# config_lock / update_config — the cross-process read-modify-write
# ---------------------------------------------------------------------------

def test_config_lock_acquires_and_cleans_up_after_itself(isolate_tempa_paths):
    lock_path = tempa_config.get_config_lock_path()
    with tempa_config.config_lock() as acquired:
        assert acquired is True
        assert lock_path.exists()
    assert not lock_path.exists()


def test_config_lock_is_exclusive_while_it_is_held(isolate_tempa_paths):
    """The whole point: a second process must not get in between another one's read and its
    write. Driven from a thread because the lock is a file, not a threading primitive — a
    second acquisition in the same process is blocked exactly as another process would be."""
    contender = []

    with tempa_config.config_lock() as acquired:
        assert acquired is True

        def contend():
            with tempa_config.config_lock(timeout=0.2) as got:
                contender.append(got)

        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(timeout=5)

    assert contender == [False], "a second holder got in while the lock was held"


def test_a_contender_that_gave_up_does_not_delete_the_held_lock(isolate_tempa_paths):
    """Fail-open must not turn into fail-destructive: the timed-out caller proceeds unlocked,
    but releasing a lock it never took would hand the file to a third caller mid-write."""
    lock_path = tempa_config.get_config_lock_path()
    with tempa_config.config_lock():
        def contend():
            with tempa_config.config_lock(timeout=0.1):
                pass

        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(timeout=5)
        assert lock_path.exists()


def test_config_lock_fails_open_rather_than_raising_when_it_cannot_be_taken(isolate_tempa_paths):
    """Refusing to write would drop a decision the user has already made. The body runs anyway,
    and the caller is told the lock wasn't held so it can say so."""
    with tempa_config.config_lock():
        result = []

        def contend():
            with tempa_config.config_lock(timeout=0.1) as got:
                result.append(got)
                result.append("body ran")

        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(timeout=5)

    assert result == [False, "body ran"]


def test_config_lock_breaks_a_lock_left_behind_by_a_dead_process(isolate_tempa_paths):
    """O_EXCL's one weakness. The critical section is a read, an assignment and a write, so a
    lock file this old is a crash, not a slow writer — and without breaking it the dashboard
    would be wedged until someone deleted the file by hand."""
    lock_path = tempa_config.get_config_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("4242 crashed", encoding="utf-8")
    stale = time.time() - (tempa_config._STALE_LOCK_SEC + 5)
    os.utime(lock_path, (stale, stale))

    with tempa_config.config_lock(timeout=1.0) as acquired:
        assert acquired is True


def test_config_lock_leaves_a_lock_that_is_still_fresh_alone(isolate_tempa_paths):
    lock_path = tempa_config.get_config_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("4242 working", encoding="utf-8")

    with tempa_config.config_lock(timeout=0.2) as acquired:
        assert acquired is False
    assert lock_path.exists()


def test_config_lock_path_follows_the_active_workspace(tmp_path, isolate_tempa_paths):
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    tempa_config.set_active_workspace_root(workspace_root)
    assert tempa_config.get_config_lock_path() == workspace_root / ".tempa" / "config.lock"


def test_update_config_reads_fresh_from_disk_inside_the_lock(isolate_tempa_paths):
    """The reason to use this over load_config/save_config: a caller holding a config it read
    earlier can never write that stale copy back over what landed in between."""
    tempa_config.save_config({"epic": [], "a": 1})
    stale = tempa_config.load_config()
    tempa_config.save_config({"epic": [], "a": 1, "written_by_someone_else": True})

    seen = {}

    def mutate(config):
        seen.update(config)
        config["answered"] = True
        return True

    assert tempa_config.update_config(mutate) is True

    on_disk = json.loads(tempa_config.get_config_path().read_text(encoding="utf-8"))
    assert seen.get("written_by_someone_else") is True, "mutator was handed a stale config"
    assert on_disk["written_by_someone_else"] is True, "the other writer's key was clobbered"
    assert on_disk["answered"] is True
    assert "written_by_someone_else" not in stale


def test_update_config_does_not_write_when_the_mutator_reports_no_change(isolate_tempa_paths):
    tempa_config.save_config({"a": 1})
    before = tempa_config.get_config_path().read_text(encoding="utf-8")

    assert tempa_config.update_config(lambda config: False) is False
    assert tempa_config.get_config_path().read_text(encoding="utf-8") == before


def test_update_config_releases_the_lock_afterwards(isolate_tempa_paths):
    tempa_config.save_config({"a": 1})
    tempa_config.update_config(lambda config: config.update({"b": 2}) or True)
    assert not tempa_config.get_config_lock_path().exists()


# ---------------------------------------------------------------------------
# Recent-workspaces history (read_workspace_history / record_workspace_history /
# remove_workspace_history) — the Home page's "recent working folders" list.
# ---------------------------------------------------------------------------
def test_read_workspace_history_empty_when_file_absent(isolate_tempa_paths):
    assert tempa_config.read_workspace_history() == []


def test_record_then_read_round_trip(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tempa_config.record_workspace_history(root)

    entries = tempa_config.read_workspace_history()
    assert len(entries) == 1
    assert entries[0]["root"] == str(root)
    assert entries[0]["opened_at"] > 0


def test_record_workspace_history_moves_existing_entry_to_front_without_duplicating(tmp_path, isolate_tempa_paths):
    a, b = tmp_path / "a", tmp_path / "b"
    tempa_config.record_workspace_history(a)
    tempa_config.record_workspace_history(b)
    tempa_config.record_workspace_history(a)

    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(a), str(b)]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="case/separator-insensitive matching is a Windows filesystem semantic; "
           "os.path.normcase is a no-op elsewhere, where these really are different paths",
)
def test_record_workspace_history_dedupes_case_and_separator_insensitively(tmp_path, isolate_tempa_paths):
    tempa_config.record_workspace_history(r"C:\A\b")
    tempa_config.record_workspace_history("c:/a/B")

    entries = tempa_config.read_workspace_history()
    assert len(entries) == 1


def test_record_workspace_history_caps_at_max_keeping_the_newest(tmp_path, isolate_tempa_paths):
    for i in range(tempa_config.WORKSPACE_HISTORY_MAX + 3):
        tempa_config.record_workspace_history(tmp_path / f"ws{i}")

    entries = tempa_config.read_workspace_history()
    assert len(entries) == tempa_config.WORKSPACE_HISTORY_MAX
    newest_first = [str(tmp_path / f"ws{i}") for i in range(
        tempa_config.WORKSPACE_HISTORY_MAX + 2, 2, -1)]
    assert [e["root"] for e in entries] == newest_first


@pytest.mark.parametrize("bad_payload", [
    "not json {{{",
    json.dumps({"not": "a list"}),
    json.dumps([{"no_root_key": True}]),
    json.dumps([{"root": ""}]),
    json.dumps(["just a string"]),
])
def test_read_workspace_history_degrades_to_empty_list_on_bad_payload(bad_payload, isolate_tempa_paths):
    tempa_config.WORKSPACE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tempa_config.WORKSPACE_HISTORY_PATH.write_text(bad_payload, encoding="utf-8")

    assert tempa_config.read_workspace_history() == []


def test_remove_workspace_history_drops_the_matching_entry(tmp_path, isolate_tempa_paths):
    a, b = tmp_path / "a", tmp_path / "b"
    tempa_config.record_workspace_history(a)
    tempa_config.record_workspace_history(b)

    assert tempa_config.remove_workspace_history(a) is True
    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(b)]


def test_remove_workspace_history_reports_false_for_unknown_path(tmp_path, isolate_tempa_paths):
    tempa_config.record_workspace_history(tmp_path / "a")
    assert tempa_config.remove_workspace_history(tmp_path / "does-not-exist") is False


def test_clear_active_workspace_root_leaves_history_untouched(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tempa_config.set_active_workspace_root(root)
    tempa_config.record_workspace_history(root)

    tempa_config.clear_active_workspace_root()

    assert tempa_config.read_workspace_history() == [
        {"root": str(root), "opened_at": tempa_config.read_workspace_history()[0]["opened_at"]}
    ]
