"""Safety net for the CLI's public surface: which subcommands exist, what flags they
accept, and that every one of them is documented in `tempa --help`.

`print_help()` is a single 160-line print statement and `_build_arg_parser()` is the only
place a command becomes reachable — the two drift apart silently (a command that works but
is undocumented, or documented but removed). These tests pin the surface itself so it
survives moving the help text or the parser into a module of their own.
"""

from __future__ import annotations

import pytest

import tempa_cli

# Every subcommand tempa answers to.
EXPECTED_COMMANDS = [
    "init", "set-folders", "show-folders", "close-folder",
    "set-model", "show-models", "set-backend", "show-backends",
    "set-effort", "show-efforts", "show-principles",
    "test", "notifications", "status", "version", "check-update", "update",
    "dashboard", "spec", "clarify", "answer", "plan", "verify", "implement", "clear",
]

# `plan` is deliberately absent from the help: planning is folded into `implement` now and
# the subcommand only survives to redirect anyone still typing it out of habit.
UNDOCUMENTED_COMMANDS = {"plan"}
DOCUMENTED_COMMANDS = [c for c in EXPECTED_COMMANDS if c not in UNDOCUMENTED_COMMANDS]


def _subcommands() -> set[str]:
    parser = tempa_cli._build_arg_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict)]
    assert actions, "the parser no longer has a subcommand group"
    return set(actions[0].choices)


def test_every_expected_subcommand_is_registered():
    assert _subcommands() == set(EXPECTED_COMMANDS)


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_help_text_documents_every_subcommand(command, capsys):
    tempa_cli.print_help()
    help_text = capsys.readouterr().out
    assert f"tempa {command}" in help_text


def test_help_text_reports_the_active_config_and_poll_interval(capsys):
    tempa_cli.print_help()
    help_text = capsys.readouterr().out
    assert "Config  :" in help_text
    assert "Work dir:" in help_text
    assert "Poll    :" in help_text
    assert "USAGE" in help_text


@pytest.mark.parametrize("argv,expected", [
    (["init", "/tmp/ws"], {"command": "init", "root": "/tmp/ws"}),
    (["set-model", "--clarify", "opus-5"], {"command": "set-model", "clarify": "opus-5"}),
    (["set-backend", "--implement", "codex"], {"command": "set-backend", "implement": "codex"}),
    (["set-effort", "--plan", "high"], {"command": "set-effort", "plan": "high"}),
    (["clarify", "--finalize"], {"command": "clarify", "finalize": True}),
    (["clarify", "--skip-minor"], {"command": "clarify", "skip_minor": True}),
    (["implement", "--replan"], {"command": "implement", "replan": True}),
    (["implement", "--reset-failed"], {"command": "implement", "reset_failed": True}),
    (["implement", "--stop-graceful"], {"command": "implement", "stop_graceful": True}),
    (["verify", "EPIC-01"], {"command": "verify", "epic": "EPIC-01"}),
    (["dashboard", "--port", "8080"], {"command": "dashboard", "port": 8080}),
    (["update", "--yes"], {"command": "update", "yes": True}),
    (["clear", "--yes"], {"command": "clear", "yes": True}),
])
def test_parser_still_accepts_each_documented_invocation(argv, expected):
    args = tempa_cli._build_arg_parser().parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value, f"{' '.join(argv)} -> {key}"
