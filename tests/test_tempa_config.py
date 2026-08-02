"""Tests for tempa_config.py — config load/save, workspace/sources/model resolution,
and the active-workspace pointer. All isolated from the real dev machine via the
autouse `isolate_tempa_paths` fixture in conftest.py."""

from __future__ import annotations

import json
import sys

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
