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
from tempa_commands import (
    print_backends,
    print_check_update,
    print_efforts,
    print_models,
    print_principles,
    print_status,
    print_version,
    print_workspace,
    run_close_folder,
    run_dashboard_command,
    run_init,
    run_spec_show,
    run_test,
    run_update,
    run_verify,
    set_backends,
    set_efforts,
    set_models,
    set_working_folders,
)
from tempa_config import (
    WORKING_DIR,
    get_backend,
    get_config_path,
    get_epic_session_id,
    get_poll_interval_sec,
    get_qa_dir,
    get_skip_minor_findings,
    load_config,
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

# Ensure UTF-8 output on Windows consoles with non-unicode code pages
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def print_help() -> None:
    config = load_config()
    sessions = (config.get("epic") or [])
    total = len(sessions)
    done = sum(1 for s in sessions if s["status"] == "done")
    on_progress = next((s for s in sessions if s["status"] == "on_progress"), None)
    failed = [s for s in sessions if s["status"] == "failed"]
    pending = sum(1 for s in sessions if s["status"] == "pending")

    print(f"""
Agent Runner — Qlar Medical Clinic Back-Office
===============================================

Config  : {get_config_path()}
Work dir: {WORKING_DIR}
Poll    : {get_poll_interval_sec(config)}s

USAGE

  -- Configuration --
  tempa init <abs>            Init project: set workspace.root + CREATE working folders on disk
                                  (existing folders are skipped, their contents are never overwritten)
  tempa set-folders --root <abs> [--docs r] [--adr r] [--specs r] [--apps r] [--infra r] [--archive r]
                                  Only set the default working folders (without creating them on disk)
  tempa show-folders         Show the active working folders (+ resolved absolute paths)
  tempa close-folder         Detach the active workspace (its config/logs/qa/specs stay put in
                                  <root>/.tempa/, untouched — reopen it later with `init` to resume)
  tempa set-model [--clarify m] [--clarify-apply m] [--plan m] [--implement m]
                                  Set the AI model per stage (alias: opus-5, sonnet-5, ... — claude only)
  tempa show-models          Show the AI model per stage
  tempa set-backend [--clarify b] [--clarify-apply b] [--plan b] [--implement b]
                                  Set the CLI backend per stage: claude | copilot | codex
  tempa show-backends        Show the CLI backend per stage
  tempa set-effort [--clarify e] [--clarify-apply e] [--plan e] [--implement e]
                                  Set the reasoning effort per stage (must be supported by that
                                  stage's backend+model; "" clears it back to the CLI/model default)
  tempa show-efforts         Show the reasoning effort per stage
  tempa show-principles      Show the architecture principles applied to every stage's prompt
                                  (optional; set them in the dashboard's Architecture Principles page)
  tempa test                 Permission test (verifies the implement stage's backend CLI runs)
  tempa notifications test   Send a test email using the configured SMTP settings
  tempa --help               Show this help

  -- Create Spec & Clarification --
  tempa clarify              Evaluate PRD clarification once (manual): write findings to file, show counts + file path,
                                  then open the dashboard on the Clarification section (add --noui to skip it)
  tempa clarify --noui       Same as above, but skip opening the dashboard
  tempa answer               Scan sources.clarifications for clarification files, and — if any finding is
                                  still unanswered — open the dashboard's Clarification section listing every
                                  such file in the left panel, without re-running clarify
  tempa clarify --auto-answer  Automatically answer unanswered clarification findings (without re-evaluating)
  tempa clarify --apply      Apply answers from the clarification files to the PRD/spec documents (without re-evaluating)
  tempa clarify --finalize   Automatic PRD clarification loop (evaluate + answer until no critical/major remain)
  tempa clarify --clear      Delete all files in .tempa/specs/clarifications except claude.md (asks for confirmation; --yes to skip)

  -- Plan & Start Implementation --
  tempa implement            Start the agent runner (polls every {get_poll_interval_sec(config)}s).
                                  If there's no task (epic/feature/QA) in config.json yet, run
                                  plan (lay out Epic/Feature/Task from the PRD) automatically first.
  tempa implement --replan   Force re-running plan first, then continue/start implementation
  tempa implement --features 4  Start with a limit of 4 features per session (overrides config)
  tempa implement --clear-plan  Clear plan: delete ALL contents of the .tempa/specs/pbi folder + empty the "epic" array (asks for confirmation; --yes to skip)
                                  (plan generation itself is now part of implement, see above)
  tempa implement --reset         Reset on_progress → pending (clears session_id)
  tempa implement --reset-failed  Reset all failed → pending
  tempa implement --reset-qa      Reset qa_passed=false for all done epics (forces QA to re-run)
  tempa implement --reset-qa EPIC-04  Same, but only for that one epic — also routes it back to
                                  require_fixing first if its features weren't all actually done
  tempa implement --clear    Delete ALL files in the workspace's .tempa/qa and .tempa/logs folders (asks for confirmation; --yes to skip)

  -- Monitoring & Utilities --
  tempa dashboard            Open the web dashboard (Home / Specification / Clarification / Implementation
                                  in a Windows-Explorer-style left panel, content on the right; Ctrl+C to stop)
  tempa spec --show          Open the dashboard directly on the Specification section: browse the PRD
                                  file/subfolder tree, view or edit any markdown file, and save changes back to disk
  tempa verify <epic>        Verify whether the epic specification has been implemented
  tempa clear                Run implement --clear + implement --clear-plan + clarify --clear together,
                                  behind a single confirmation (asks for confirmation; --yes to skip)
  tempa status               Show a progress summary of all sessions
  tempa version              Show the locally installed Tempa version
  tempa check-update         Check GitHub for the latest released version and compare it to
                                  the installed one
  tempa update [--yes]       If a newer release exists, download it and overwrite this
                                  install's files with it (asks for confirmation; --yes to skip)

GLOBAL FLAGS
  --show-prompt                   Show the prompt sent to the backend CLI on the console (default: off; the prompt is always recorded to the log). Applies to every command that runs a session — pass it AFTER the subcommand, e.g. `tempa implement --show-prompt`.

CONFIG OPTIONS (config.json)
  features_per_session            Max features per session (null = no limit)
  workspace.root                  Root folder (MUST be absolute) — every other folder is relative to this
  workspace.docs                  Current application documentation folder (default: docs)
  workspace.adr                   Architecture decision record folder (default: adr)
  workspace.specs                 NEW specification folder to be worked on (default: specs, stored
                                      under <root>/.tempa/ rather than directly under root)
  workspace.apps                  Application implementation folder (default: src)
  workspace.infra                 Infrastructure scripts folder, e.g. docker compose (default: infra)
  workspace.archive                Archive folder for old, unused specifications (default: archive)
  sources.*                       DERIVED from workspace.* by default (see below); set a key here to override it
                                      (relative to workspace.root, absolute paths also supported)
  sources.prd                     PRD folder = the NEW specification to be worked on (default: workspace.specs/prd,
                                      i.e. <root>/.tempa/specs/prd)
  sources.docs                    CURRENT system documentation folder, reference for 'what already exists'
                                      (default: workspace.docs)
  sources.epics                   Path to the epic spec folder, plan output, run via implement
                                      (default: workspace.specs/pbi/epics, i.e. <root>/.tempa/specs/pbi/epics)
  sources.apps                    Monorepo root, ALL services; each service's src & tests live inside it
                                      (default: workspace.apps)
  sources.clarifications          Clarification results folder (default: workspace.specs/clarifications,
                                      i.e. <root>/.tempa/specs/clarifications)
  models.clarify                  AI model for clarify (default: claude-opus-5)
  models.plan                     AI model for the plan stage, run via implement (default: claude-sonnet-5)
  models.implement                AI model for implement/QA/verify (default: claude-sonnet-5)
  backends.clarify                CLI backend for clarify: claude | copilot | codex (default: claude)
  backends.plan                   CLI backend for the plan stage, run via implement (default: claude)
  backends.implement              CLI backend for implement/QA/verify (default: claude)
  reasoning_efforts.clarify       Reasoning effort for clarify ("" = backend/model default)
  reasoning_efforts.plan          Reasoning effort for the plan stage, run via implement
  reasoning_efforts.implement     Reasoning effort for implement/QA/verify

PROMPT TEMPLATES (src/prompt/ folder, one .md file per prompt — no longer in config.json)
  src/prompt/implementation.md        New implementation prompt
  src/prompt/continuation.md          Prompt for resuming a session (fallback: implementation.md)
  src/prompt/verify.md                Implementation verification prompt (verify)
  src/prompt/qa.md                    Automatic QA prompt (run after an epic is done)
  src/prompt/qa_continuation.md       Prompt for resuming QA (fallback: qa.md)
  src/prompt/clarification.md         PRD clarification evaluation prompt (clarify)
  src/prompt/auto_answer.md           Prompt for answering unanswered findings (clarify --auto-answer)
  src/prompt/apply_clarification.md   Prompt for applying clarification resolutions (clarify --finalize)
  src/prompt/plan_epics.md            Prompt to generate new epics/features/tasks (plan, run via implement)
  src/prompt/review_epics.md          Prompt to review & fix the result (plan, run via implement)
  Available placeholders: ${{epic}}, ${{sources}}, ${{sources.<key>}}, ${{config_path}},
  ${{output_file}}, ${{qa_output_file}} (depends on the prompt).
  The workspace's architecture principles (.tempa/architecture-principles.md) are prepended to
  EVERY prompt above automatically — no placeholder needed. See: tempa show-principles

SESSION STATUS
  pending        Not started yet
  on_progress    Currently running
  done           Done (set by the agent)
  require_fixing Already implemented but has QA findings — will be fixed automatically
  failed         Error — fix it then run implement --reset-failed

QA STATUS (qa_passed field per epic)
  false (🔍)   QA has not run yet — the runner will run QA before the next implementation
  true  (✅)   QA has passed — the next epic's implementation may start
  QA reports are saved at: {get_qa_dir()}

PROGRESS ({done}/{total} epics done)""")

    if on_progress:
        label = on_progress.get("epic_name", "?")
        total_f = on_progress.get("total_features", 0)
        completed_f = on_progress.get("completed_features", 0)
        progress_str = f"{completed_f}/{total_f}" if total_f else "?"
        backend = get_backend(config, "implement")
        sid = get_epic_session_id(on_progress, backend, kind="implement") or "-"
        print(f"  IN PROGRESS : {label} ({progress_str} features) — backend: {backend} — session_id: {sid}")
    if failed:
        for s in failed:
            print(f"  FAILED      : {s.get('epic_name', '?')}")
    print(f"  Pending     : {pending} session(s)")
    print()


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

    p = sub.add_parser("clear", parents=[common], add_help=False)
    p.add_argument("--yes", action="store_true")

    return parser


def _dispatch_clarify(args: argparse.Namespace) -> None:
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
