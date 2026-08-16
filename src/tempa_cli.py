"""Tempa CLI entry point.

Thin dispatch layer: parse the command line, show help, and route each subcommand to its
handler in the tempa_* modules. The actual work lives in tempa_config (paths + config I/O),
tempa_logging (runner state + logging), tempa_prompts (prompt construction), tempa_backend
(per-CLI backend adapters: Claude Code / GitHub Copilot CLI / OpenAI Codex CLI), tempa_session
(the agent-runner session engine, driven by tempa_backend), tempa_implement (the implement
loop), tempa_clarify (clarify), tempa_maintenance (clear/reset), and tempa_commands
(workspace/model/backend/status/spec/verify/test).

Imported and invoked (via run()) by the root tempa.py launcher, which puts this src/ folder
on sys.path so the sibling tempa_*/dashboard_* modules import by top-level name. That keeps
Tempa runnable as a plain script — `py tempa.py <cmd>` — from any working directory.
"""

from __future__ import annotations

import argparse
import sys

from tempa_clarify import (
    run_answer_command,
    run_clarify_answer,
    run_clarify_apply,
    run_clarify_finalize,
    run_clarify_once,
)
from tempa_cli_help import print_help
from tempa_commands import (
    print_backends,
    print_efforts,
    print_models,
    print_principles,
    print_status,
    print_workspace,
    run_close_folder,
    run_dashboard_command,
    run_init,
    run_spec_show,
    run_test,
    run_verify,
    set_backends,
    set_efforts,
    set_models,
    set_working_folders,
)
from tempa_config import (
    clear_graceful_stop,
    get_skip_minor_findings,
    load_config,
    request_graceful_stop,
)
from tempa_implement import main
from tempa_maintenance import (
    _reset_failed_epics,
    _reset_on_progress_epics,
    _reset_qa_state,
    run_clarify_clear,
    run_clear_all,
    run_implement_clear,
    run_plan_clear,
)
from tempa_notifications import send_test_email
from tempa_process_group import install_process_cleanup_handlers
from tempa_update import print_check_update, print_version, run_update

# Ensure UTF-8 output on Windows consoles with non-unicode code pages
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the subcommand parser. `-h`/`--help` is intentionally NOT registered here —
    see __main__, which checks for it in raw sys.argv before parsing at all, so it always
    shows the same rich hand-written help (print_help()) regardless of what else is on the
    command line, the same way it did before this was converted to argparse."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--show-prompt", action="store_true", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="tempa.py", add_help=False)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", parents=[common], add_help=False)
    p.add_argument("root", nargs="?", default=None)

    p = sub.add_parser("set-folders", parents=[common], add_help=False)
    for flag in ("--root", "--docs", "--adr", "--specs", "--apps", "--infra", "--archive"):
        p.add_argument(flag)

    sub.add_parser("show-folders", parents=[common], add_help=False)

    sub.add_parser("close-folder", parents=[common], add_help=False)

    p = sub.add_parser("set-model", parents=[common], add_help=False)
    p.add_argument("--clarify")
    p.add_argument("--clarify-apply")
    p.add_argument("--plan")
    p.add_argument("--implement")

    sub.add_parser("show-models", parents=[common], add_help=False)

    p = sub.add_parser("set-backend", parents=[common], add_help=False)
    p.add_argument("--clarify")
    p.add_argument("--clarify-apply")
    p.add_argument("--plan")
    p.add_argument("--implement")

    sub.add_parser("show-backends", parents=[common], add_help=False)

    p = sub.add_parser("set-effort", parents=[common], add_help=False)
    p.add_argument("--clarify")
    p.add_argument("--clarify-apply")
    p.add_argument("--plan")
    p.add_argument("--implement")

    sub.add_parser("show-efforts", parents=[common], add_help=False)
    sub.add_parser("show-principles", parents=[common], add_help=False)
    sub.add_parser("test", parents=[common], add_help=False)
    p = sub.add_parser("notifications", parents=[common], add_help=False)
    p.add_argument("action", choices=("test",))
    sub.add_parser("status", parents=[common], add_help=False)
    sub.add_parser("version", parents=[common], add_help=False)
    sub.add_parser("check-update", parents=[common], add_help=False)

    p = sub.add_parser("update", parents=[common], add_help=False)
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("dashboard", parents=[common], add_help=False)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("spec", parents=[common], add_help=False)
    p.add_argument("--show", action="store_true")

    p = sub.add_parser("clarify", parents=[common], add_help=False)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--finalize", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--auto-answer", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--noui", action="store_true")
    p.add_argument("--skip-minor", action="store_true")
    p.add_argument("--stop-graceful", action="store_true")
    p.add_argument("--stop-graceful-cancel", action="store_true")

    sub.add_parser("answer", parents=[common], add_help=False)

    # Deprecated: plan generation is now folded into `implement`. Kept as a subcommand
    # purely to redirect anyone who still types it out of habit.
    sub.add_parser("plan", parents=[common], add_help=False)

    p = sub.add_parser("verify", parents=[common], add_help=False)
    p.add_argument("epic")

    p = sub.add_parser("implement", parents=[common], add_help=False)
    p.add_argument("--reset-failed", action="store_true")
    p.add_argument("--reset-qa", nargs="?", const=True, default=False, metavar="EPIC")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--clear-plan", action="store_true")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--features")
    p.add_argument("--replan", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--stop-graceful", action="store_true")
    p.add_argument("--stop-graceful-cancel", action="store_true")

    p = sub.add_parser("clear", parents=[common], add_help=False)
    p.add_argument("--yes", action="store_true")

    return parser


def _dispatch_clarify(args: argparse.Namespace) -> None:
    # Checked first: these only leave (or withdraw) a request for a run happening
    # elsewhere, and must never fall through into starting a clarification of their own.
    if args.stop_graceful:
        request_graceful_stop("clarify")
        print("Graceful stop requested for clarification.")
        print("A running Finalized Clarification will stop once the round in progress "
              "finishes; Apply Answers will stop after the session in progress finishes "
              "— nothing already paid for is thrown away.")
        print("If nothing is running, this request is cleared automatically the next "
              "time clarification starts.")
        print("Cancel with:  tempa clarify --stop-graceful-cancel")
        return
    if args.stop_graceful_cancel:
        clear_graceful_stop("clarify")
        print("Pending graceful stop for clarification cancelled — a run in progress "
              "will continue normally.")
        return
    if args.clear:
        run_clarify_clear()
    elif args.finalize:
        skip_minor = args.skip_minor or get_skip_minor_findings(load_config())
        run_clarify_finalize(skip_minor=skip_minor)
    elif args.apply:
        run_clarify_apply()
    elif args.auto_answer:
        run_clarify_answer()
    else:
        skip_minor = args.skip_minor or get_skip_minor_findings(load_config())
        run_clarify_once(noui=args.noui, skip_minor=skip_minor)


def _dispatch_implement(args: argparse.Namespace) -> None:
    # Checked first, for the same reason as the clarify pair: leaving a request must
    # never be able to start a runner of its own.
    if args.stop_graceful:
        request_graceful_stop("implement")
        print("Graceful stop requested for implementation.")
        print("A running `tempa implement` will stop once the session in progress "
              "(feature or QA) finishes — nothing already paid for is thrown away.")
        print("If nothing is running, this request is cleared automatically the next "
              "time implementation starts.")
        print("Cancel with:  tempa implement --stop-graceful-cancel")
        return
    if args.stop_graceful_cancel:
        clear_graceful_stop("implement")
        print("Pending graceful stop for implementation cancelled — the runner will "
              "continue normally.")
        return
    if args.reset_failed:
        _reset_failed_epics()
    elif args.reset_qa:
        _reset_qa_state(None if args.reset_qa is True else args.reset_qa)
    elif args.reset:
        _reset_on_progress_epics()
    elif args.clear_plan:
        run_plan_clear()
    elif args.clear:
        run_implement_clear()
    else:
        features_override = None
        if args.features is not None:
            try:
                features_override = int(args.features)
            except ValueError:
                print(f"--features must be a number, not '{args.features}'")
                sys.exit(1)
        main(features_override=features_override, replan=args.replan)


def run() -> None:
    """CLI entry point, called by the root tempa.py launcher."""
    # Checked in raw argv, before argparse ever runs, so --help always shows the same
    # hand-written help regardless of what other (possibly invalid) flags are present.
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)

    # Ctrl+C and the dashboard's Stop button have to reach the backend CLI's whole process
    # tree, not just Tempa itself — see tempa_process_group for why that stops being
    # automatic once a session is contained.
    install_process_cleanup_handlers()

    cli_args = _build_arg_parser().parse_args()

    if cli_args.command is None:
        print("No command given. Run 'tempa --help' for usage.")
        sys.exit(1)
    elif cli_args.command == "status":
        print_status()
    elif cli_args.command == "init":
        run_init(cli_args)
    elif cli_args.command == "set-folders":
        set_working_folders(cli_args)
    elif cli_args.command == "show-folders":
        print_workspace()
    elif cli_args.command == "close-folder":
        run_close_folder()
    elif cli_args.command == "set-model":
        set_models(cli_args)
    elif cli_args.command == "show-models":
        print_models()
    elif cli_args.command == "set-backend":
        set_backends(cli_args)
    elif cli_args.command == "show-backends":
        print_backends()
    elif cli_args.command == "set-effort":
        set_efforts(cli_args)
    elif cli_args.command == "show-efforts":
        print_efforts()
    elif cli_args.command == "show-principles":
        print_principles()
    elif cli_args.command == "test":
        run_test()
    elif cli_args.command == "notifications":
        ok, message = send_test_email()
        print(message)
        if not ok:
            sys.exit(1)
    elif cli_args.command == "version":
        print_version()
    elif cli_args.command == "check-update":
        print_check_update()
    elif cli_args.command == "update":
        run_update()
    elif cli_args.command == "dashboard":
        run_dashboard_command(port=cli_args.port, open_browser=not cli_args.no_browser)
    elif cli_args.command == "spec":
        if not cli_args.show:
            print("Usage: tempa spec --show")
            sys.exit(1)
        run_spec_show()
    elif cli_args.command == "clarify":
        _dispatch_clarify(cli_args)
    elif cli_args.command == "answer":
        run_answer_command()
    elif cli_args.command == "plan":
        print("Plan is now run automatically by 'tempa implement' (when there's no "
              "epic/feature/QA task yet), or force it with 'tempa implement --replan'.")
        print("To clear a previous plan result: tempa implement --clear-plan")
        sys.exit(1)
    elif cli_args.command == "verify":
        run_verify(cli_args.epic)
    elif cli_args.command == "implement":
        _dispatch_implement(cli_args)
    elif cli_args.command == "clear":
        run_clear_all()
