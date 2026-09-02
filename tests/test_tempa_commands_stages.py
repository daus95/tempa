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
    # The model has to move first: set_backends refuses a pair the CLI could not run, and
    # clarify_apply's default model is Anthropic's (see the migration tests below).
    tempa_config.save_config({"models": {"clarify_apply": "gpt-5.6-sol"}})
    tcmd.set_backends(_args(clarify="claude", clarify_apply="codex"))
    config = tempa_config.load_config()
    assert config["backends"]["clarify"] == "claude"
    assert config["backends"]["clarify_apply"] == "codex"


def test_set_backends_rejects_unknown_clarify_apply_value(isolate_tempa_paths):
    import pytest
    with pytest.raises(SystemExit):
        tcmd.set_backends(_args(clarify_apply="not-a-backend"))


# ---------------------------------------------------------------------------
# Backend/model compatibility
# ---------------------------------------------------------------------------

def test_set_backends_rejects_a_backend_that_cannot_run_the_stored_model(isolate_tempa_paths):
    import pytest
    tempa_config.save_config({
        "backends": {"clarify": "claude"},
        "models": {"clarify": "claude-opus-5"},
    })
    with pytest.raises(SystemExit):
        tcmd.set_backends(_args(clarify="codex"))
    # Rejected before save_config, so nothing was written.
    assert tempa_config.load_config()["backends"]["clarify"] == "claude"


def test_set_backends_accepts_a_backend_that_serves_the_stored_model(isolate_tempa_paths):
    # copilot proxies both vendors, so an Anthropic model on it is a valid pair.
    tempa_config.save_config({
        "backends": {"clarify": "claude"},
        "models": {"clarify": "claude-opus-5"},
    })
    tcmd.set_backends(_args(clarify="copilot"))
    assert tempa_config.load_config()["backends"]["clarify"] == "copilot"


def test_set_models_warns_but_still_writes_a_transitional_value(isolate_tempa_paths, capsys):
    """The escape hatch that keeps a stage migratable: set-model accepts the half-finished
    pair (loudly), so `set-model` then `set-backend` always works. The pair still cannot
    run — prepare_backend_invocation refuses it before spawning anything."""
    tempa_config.save_config({
        "backends": {"clarify": "claude"},
        "models": {"clarify": "claude-opus-5"},
    })
    tcmd.set_models(_args(clarify="gpt-5.6-sol"))
    assert tempa_config.load_config()["models"]["clarify"] == "gpt-5.6-sol"
    assert "WARNING" in capsys.readouterr().out
    # ...and now the backend half of the migration is accepted.
    tcmd.set_backends(_args(clarify="codex"))
    assert tempa_config.load_config()["backends"]["clarify"] == "codex"


def test_show_commands_flag_a_mismatched_stage_from_each_tables_perspective(isolate_tempa_paths, capsys):
    """Each table names the OTHER half of the pair: a backends table saying "not runnable on
    OpenAI Codex CLI" next to a row that already says OpenAI Codex CLI reads as the backend
    failing against itself."""
    tempa_config.save_config({
        "backends": {"plan": "codex"},
        "models": {"plan": "claude-opus-5"},
    })
    tcmd.print_models()
    tcmd.print_backends()
    out = capsys.readouterr().out
    assert "[!] not runnable on OpenAI Codex CLI" in out
    assert "[!] cannot run model 'claude-opus-5'" in out


def test_show_commands_flag_nothing_when_every_pair_is_fine(isolate_tempa_paths, capsys):
    tempa_config.save_config({
        "backends": {s: "claude" for s in ("clarify", "clarify_apply", "plan", "implement")},
        "models": {s: "claude-sonnet-5" for s in ("clarify", "clarify_apply", "plan", "implement")},
    })
    tcmd.print_models()
    tcmd.print_backends()
    assert "[!]" not in capsys.readouterr().out


def test_set_models_does_not_warn_about_an_unrecognized_model_id(isolate_tempa_paths, capsys):
    """The model field is free text: an id from no known vendor family is nobody's to
    reject, so a future or private model keeps working."""
    tempa_config.save_config({"backends": {"clarify": "codex"}, "models": {"clarify": "gpt-5.6-sol"}})
    tcmd.set_models(_args(clarify="some-internal-model"))
    assert tempa_config.load_config()["models"]["clarify"] == "some-internal-model"
    assert "WARNING" not in capsys.readouterr().out


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
