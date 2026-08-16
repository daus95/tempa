"""The hand-written `tempa --help` screen.

Not argparse's generated help: it is a single formatted page covering every command, the
config.json keys, the folder layout, and the prompt templates, ending with a live status
summary of the active workspace's plan. `tempa_cli` deliberately doesn't register
`-h`/`--help` with argparse at all (see _build_arg_parser) so this is what any form of
`--help` prints, whatever else is on the command line.

It lives in its own module purely because of its size — 170 lines of text that would
otherwise be most of tempa_cli.py, which is meant to be a dispatch layer.
"""

from __future__ import annotations

from tempa_config import (
    WORKING_DIR,
    get_backend,
    get_config_path,
    get_epic_session_id,
    get_poll_interval_sec,
    get_qa_dir,
    load_config,
)


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
  tempa clarify --stop-graceful  Ask a running clarification to stop at its next safe point — after the
                                  current finalize round / apply session — instead of killing it mid-session
  tempa clarify --stop-graceful-cancel  Withdraw that request; the run continues normally

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
  tempa implement --stop-graceful  Ask a running agent runner to stop once the session in progress
                                  (feature or QA) finishes, instead of killing it mid-session
  tempa implement --stop-graceful-cancel  Withdraw that request; the runner continues normally

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
