"""Tests for tempa_commands.py's per-stage set-model/set-backend/set-effort commands,
focused on "clarify_apply" — a full stage in its own right (its own backend + reasoning
effort, not just a model that piggybacks on "clarify"'s)."""

from __future__ import annotations

import argparse

import tempa_commands as tcmd
import tempa_config


def _args(**overrides) -> argparse.Namespace:
    """Namespace with every stage flag defaulting to None (the CLI's "not given" value),
    overridden by whatever the caller passes."""
    base = {"clarify": None, "clarify_apply": None, "plan": None, "implement": None}
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# set_models
# ---------------------------------------------------------------------------

def test_set_models_clarify_apply_saved_independently_of_clarify(isolate_tempa_paths):
    tcmd.set_models(_args(clarify="opus-5", clarify_apply="sonnet-5"))
    config = tempa_config.load_config()
    assert config["models"]["clarify"] == "claude-opus-5"
    assert config["models"]["clarify_apply"] == "claude-sonnet-5"


def test_set_models_clarify_apply_alias_resolved_against_its_own_backend(isolate_tempa_paths):
    # clarify_apply's backend is codex (non-claude) -> its model value is stored as-is,
    # NOT alias-resolved, even though clarify's backend is claude.
    tempa_config.save_config({"backends": {"clarify": "claude", "clarify_apply": "codex"}})
    tcmd.set_models(_args(clarify_apply="gpt-5.6-sol"))
    config = tempa_config.load_config()
    assert config["models"]["clarify_apply"] == "gpt-5.6-sol"


# ---------------------------------------------------------------------------
# set_backends
# ---------------------------------------------------------------------------

def test_set_backends_clarify_apply_independent_of_clarify(isolate_tempa_paths):
    tcmd.set_backends(_args(clarify="claude", clarify_apply="codex"))
    config = tempa_config.load_config()
    assert config["backends"]["clarify"] == "claude"
    assert config["backends"]["clarify_apply"] == "codex"


def test_set_backends_rejects_unknown_clarify_apply_value(isolate_tempa_paths):
    import pytest
    with pytest.raises(SystemExit):
        tcmd.set_backends(_args(clarify_apply="not-a-backend"))


# ---------------------------------------------------------------------------
# set_efforts
# ---------------------------------------------------------------------------

def test_set_efforts_clarify_apply_independent_of_clarify(isolate_tempa_paths):
    tempa_config.save_config({
        "backends": {"clarify": "claude", "clarify_apply": "claude"},
        "models": {"clarify": "claude-opus-5", "clarify_apply": "claude-sonnet-5"},
    })
    tcmd.set_efforts(_args(clarify="high", clarify_apply="low"))
    config = tempa_config.load_config()
    assert config["reasoning_efforts"]["clarify"] == "high"
    assert config["reasoning_efforts"]["clarify_apply"] == "low"


def test_set_efforts_rejects_effort_unsupported_by_clarify_apply_backend_model(isolate_tempa_paths):
    import pytest
    # copilot's valid levels don't include "ultra".
    tempa_config.save_config({
        "backends": {"clarify_apply": "copilot"},
        "models": {"clarify_apply": "claude-sonnet-5"},
    })
    with pytest.raises(SystemExit):
        tcmd.set_efforts(_args(clarify_apply="ultra"))
