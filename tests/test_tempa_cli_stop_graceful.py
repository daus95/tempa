"""Tests for the CLI half of graceful stop — `tempa implement --stop-graceful` and the
`clarify` equivalent, plus their `--stop-graceful-cancel` counterparts.

These flags exist so a graceful stop isn't dashboard-only: the sentinel they write is the
same file the dashboard writes, resolved through the same active workspace, so a request
made in a terminal stops a run started from the dashboard and vice versa.

The one behaviour worth locking down beyond the round trip is that they are checked FIRST
in each dispatcher: leaving a request must never fall through into starting a
clarification or an agent runner of its own.
"""

from __future__ import annotations

import argparse

import pytest

import tempa_cli
import tempa_config


def _implement_args(**overrides) -> argparse.Namespace:
    args = argparse.Namespace(
        stop_graceful=False, stop_graceful_cancel=False, reset_failed=False,
        reset_qa=False, reset=False, clear_plan=False, clear=False,
        features=None, replan=False, yes=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _clarify_args(**overrides) -> argparse.Namespace:
    args = argparse.Namespace(
        stop_graceful=False, stop_graceful_cancel=False, clear=False, finalize=False,
        apply=False, auto_answer=False, yes=False, noui=False, skip_minor=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _fail_if_called(name):
    def _boom(*args, **kwargs):
        pytest.fail(f"{name} must not run for a --stop-graceful* invocation")
    return _boom


@pytest.fixture(autouse=True)
def no_runs_may_start(monkeypatch):
    """Every heavy entry point either dispatcher can reach, stubbed to fail loudly."""
    for name in ("main", "_reset_failed_epics", "_reset_qa_state", "_reset_on_progress_epics",
                 "run_plan_clear", "run_implement_clear", "run_clarify_clear",
                 "run_clarify_finalize", "run_clarify_apply", "run_clarify_answer",
                 "run_clarify_once"):
        monkeypatch.setattr(tempa_cli, name, _fail_if_called(name))


def test_implement_stop_graceful_writes_the_sentinel(isolate_tempa_paths, capsys):
    tempa_cli._dispatch_implement(_implement_args(stop_graceful=True))

    assert tempa_config.graceful_stop_requested("implement") is True
    out = capsys.readouterr().out
    assert "Graceful stop requested for implementation." in out
    assert "--stop-graceful-cancel" in out  # the way back out is always shown


def test_implement_stop_graceful_cancel_clears_the_sentinel(isolate_tempa_paths):
    tempa_config.request_graceful_stop("implement")

    tempa_cli._dispatch_implement(_implement_args(stop_graceful_cancel=True))

    assert tempa_config.graceful_stop_requested("implement") is False


def test_implement_stop_graceful_cancel_is_safe_with_nothing_pending(isolate_tempa_paths):
    tempa_cli._dispatch_implement(_implement_args(stop_graceful_cancel=True))
    assert tempa_config.graceful_stop_requested("implement") is False


def test_implement_stop_graceful_wins_over_other_flags(isolate_tempa_paths):
    # The autouse fixture makes _reset_failed_epics fail the test if it is reached.
    tempa_cli._dispatch_implement(_implement_args(stop_graceful=True, reset_failed=True))
    assert tempa_config.graceful_stop_requested("implement") is True


def test_clarify_stop_graceful_writes_the_sentinel(isolate_tempa_paths, capsys):
    tempa_cli._dispatch_clarify(_clarify_args(stop_graceful=True))

    assert tempa_config.graceful_stop_requested("clarify") is True
    assert "Graceful stop requested for clarification." in capsys.readouterr().out


def test_clarify_stop_graceful_cancel_clears_the_sentinel(isolate_tempa_paths):
    tempa_config.request_graceful_stop("clarify")

    tempa_cli._dispatch_clarify(_clarify_args(stop_graceful_cancel=True))

    assert tempa_config.graceful_stop_requested("clarify") is False


def test_clarify_stop_graceful_wins_over_other_flags(isolate_tempa_paths):
    tempa_cli._dispatch_clarify(_clarify_args(stop_graceful=True, finalize=True))
    assert tempa_config.graceful_stop_requested("clarify") is True


def test_clarify_stop_graceful_does_not_touch_implement(isolate_tempa_paths):
    tempa_cli._dispatch_clarify(_clarify_args(stop_graceful=True))
    assert tempa_config.graceful_stop_requested("implement") is False


@pytest.mark.parametrize("command,flag", [
    ("implement", "--stop-graceful"),
    ("implement", "--stop-graceful-cancel"),
    ("clarify", "--stop-graceful"),
    ("clarify", "--stop-graceful-cancel"),
])
def test_flags_are_accepted_by_the_parser(command, flag):
    args = tempa_cli._build_arg_parser().parse_args([command, flag])
    attribute = flag.lstrip("-").replace("-", "_")
    assert getattr(args, attribute) is True
