"""The clarify workflow: evaluate the PRD for ambiguities, answer, and apply.

One-pass evaluate (`run_clarify_once`), auto-answer (`run_clarify_answer`), apply resolutions
to the PRD (`run_clarify_apply`), the evaluate+apply loop (`run_clarify_finalize`), and opening
the answer dashboard (`run_answer_command`). Session running lives in tempa_session; prompt
construction in tempa_prompts; this module orchestrates them and interprets config.json's
last_clarification_* state.
"""

from __future__ import annotations

import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

from dashboard_clarify_parse import file_answer_status
from dashboard_ui import run_dashboard
from tempa_backend import get_backend_def
from tempa_config import get_backend, get_model, get_sources, load_config, save_config
from tempa_config import resolve_prd_dir as _resolve_prd_dir
from tempa_logging import _banner, _hyperlink, _init_process_log, _state, log
from tempa_prompts import (
    build_apply_clarification_prompt,
    build_auto_answer_prompt,
    build_clarification_prompt,
)
from tempa_session import run_apply_clarification_session, run_clarification_session


def _clarification_report_files(folder: Path, since: float) -> list[Path]:
    """Return .md files in `folder` last modified at/after `since` (epoch seconds) —
    i.e. the report files produced/updated by the evaluation that just ran."""
    if not folder.exists():
        return []
    out: list[Path] = []
    for p in sorted(folder.glob("*.md")):
        try:
            if p.stat().st_mtime >= since:
                out.append(p)
        except OSError:
            pass
    return out


def run_clarify_once(noui: bool = False) -> None:
    """Manual clarification — run ONE evaluation pass, report findings + report file(s),
    then suggest the next step based on severity:
      - critical==0 & major==0 : clarification done → suggest moving on to implement (auto plan)
      - critical==0 (major>0)  : suggest answering manually, or finishing with clarify --finalize
      - critical>0             : suggest reviewing/answering manually then clarify again
    Unless `noui` is set, also opens the clarification-answer web UI on the freshly
    written report file(s) so the user can answer right away instead of hand-editing
    the markdown."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    clar_dir.mkdir(parents=True, exist_ok=True)

    _banner(f"Clarify (manual) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    start_ts = time.time() - 1  # small epsilon so freshly-written files are caught
    prompt = build_clarification_prompt(config)
    if not run_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify stopped — usage limit reached.")
            sys.exit(2)
        log("Clarification evaluation failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Clarify stopped — usage limit reached.")
        sys.exit(2)

    config = load_config()
    findings = config.get("last_clarification_findings", {})
    critical = findings.get("critical", 0)
    major = findings.get("major", 0)
    minor = findings.get("minor", 0)
    # Stamps *how* the current last_clarification_findings was produced — an
    # evaluate pass here, vs an apply pass in _run_apply_step() — so the dashboard's
    # finalize gate can tell "criticals were answered and applied" apart from "a
    # fresh evaluation independently confirmed 0 criticals remain".
    config["last_clarification_action"] = "evaluate"
    config["last_clarification_round"] = config.get("last_clarification_round", 0) + 1
    save_config(config)
    report_files = _clarification_report_files(clar_dir, start_ts)

    _banner(f"CLARIFICATION EVALUATION RESULT — critical={critical} major={major} minor={minor}")
    if report_files:
        for f in report_files:
            print(f"  {_hyperlink(f)}", flush=True)
    else:
        print(f"  (No new file detected — check the folder manually: {clarifications_path})", flush=True)

    if critical == 0 and major == 0:
        print("[OK] No critical/major findings — clarification is considered DONE.", flush=True)
        if minor:
            print(f"     (Still {minor} minor finding(s) — considered acceptable.)", flush=True)
        print("     Move on to the next stage:  tempa implement  (auto plan runs first)", flush=True)
    elif critical == 0:
        print(f"Only {major} major finding(s) remain (no critical). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Apply the answers to the PRD/spec:                      tempa clarify --apply", flush=True)
        print("  (Or do both at once, evaluate+apply loop:                 tempa clarify --finalize)", flush=True)
    else:
        print(f"[!] There are {critical} critical finding(s). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Apply the answers to the PRD/spec:                      tempa clarify --apply", flush=True)
        print("  Then repeat tempa clarify to verify.", flush=True)

    if not noui and report_files:
        saved = run_dashboard(_resolve_prd_dir(config), clar_dir, initial_view="clarification")
        if saved:
            log("Answers saved. Run `tempa clarify --apply` when you're ready to apply them to the PRD/spec.")

    sys.exit(0)


def run_clarify_answer() -> None:
    """Auto-answer — fill in answers for clarification findings that are NOT yet answered
    (one pass). Does not re-evaluate / look for new findings. If every finding already has
    an answer, report that there is nothing left to answer."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    existing = sorted(clar_dir.glob("*.md")) if clar_dir.exists() else []
    if not existing:
        log("No clarification results to answer yet. Run first: tempa clarify")
        sys.exit(0)

    _banner(f"Clarify (auto-answer) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    # Reset the marker so a stale value from a previous run isn't misread.
    config["last_auto_answer"] = 0
    save_config(config)

    start_ts = time.time() - 1
    prompt = build_auto_answer_prompt(config)
    if not run_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Auto-answer stopped — usage limit reached.")
            sys.exit(2)
        log("Auto-answer failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Auto-answer stopped — usage limit reached.")
        sys.exit(2)

    config = load_config()
    answered = config.get("last_auto_answer", 0)
    changed = _clarification_report_files(clar_dir, start_ts)

    if isinstance(answered, int) and answered > 0:
        print(f"[OK] {answered} clarification finding(s) answered automatically.", flush=True)
        for f in changed:
            print(f"  {_hyperlink(f)}", flush=True)
    else:
        print("[OK] Every clarification finding already has an answer — nothing left to answer.", flush=True)
    sys.exit(0)


def run_clarify_finalize() -> None:
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")

    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    Path(clarifications_path).mkdir(parents=True, exist_ok=True)

    max_run = config.get("max_clarification_run", 20)
    run_number = 0

    _banner(f"Clarify (finalize) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path} | max_runs={max_run}")

    while run_number < max_run:
        run_number += 1

        round_header = f"ROUND {run_number}/{max_run} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        _banner(round_header)
        log(round_header, to_console=False)

        config = load_config()
        prompt = build_clarification_prompt(config)

        success = run_clarification_session(prompt, run_number, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify"))
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify (finalize) stopped — usage limit reached.")
            sys.exit(2)
        if not success:
            log(f"Clarification run #{run_number} failed — stopping the loop.")
            sys.exit(1)

        config = load_config()
        findings = config.get("last_clarification_findings", {})
        critical = findings.get("critical", 0)
        major = findings.get("major", 0)
        minor = findings.get("minor", 0)
        config["last_clarification_action"] = "evaluate"
        config["last_clarification_round"] = run_number
        save_config(config)

        log(f"Round #{run_number} findings: critical={critical}, major={major}, minor={minor}")

        if critical == 0 and major == 0:
            log("No critical/major findings. Clarify (finalize) done.")
            if minor > 0:
                log(f"Still {minor} minor finding(s) — considered acceptable.")
            sys.exit(0)

        log(f"Still {critical} critical and {major} major findings remain — applying resolutions to the PRD/spec documents...")

        config = load_config()
        apply_prompt = build_apply_clarification_prompt(config)
        apply_success = run_apply_clarification_session(apply_prompt, run_number, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify"))
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify (finalize) stopped — usage limit reached.")
            sys.exit(2)
        if not apply_success:
            log(f"Apply-clarification run #{run_number} failed — stopping the loop.")
            sys.exit(1)

        config = load_config()
        config["last_clarification_action"] = "apply"
        save_config(config)

        log("Resolutions applied. Running re-evaluation...")

    log(f"Clarify (finalize) reached the {max_run}-run limit. Stopping.")
    sys.exit(1)


def _record_clarify_applied_state(config: dict, clar_dir: Path) -> None:
    """Stamp every current clarification result file's content hash into
    config["clarify_applied_hashes"] right after a successful apply — the dashboard
    compares each file's live hash against this to know whether its currently-recorded
    answers have already been applied to the PRD/spec, or have changed (or never been
    applied) since. Applying doesn't touch the clarification files themselves (only the
    PRD/spec + config), so this is the only record of "applied" state there is."""
    hashes = {}
    for p in _clarification_result_files(clar_dir):
        try:
            hashes[p.name] = hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        except OSError:
            continue
    config["clarify_applied_hashes"] = hashes
    save_config(config)


def _run_apply_step(config: dict) -> bool:
    """Run one apply-clarification session (writes answers/resolutions into the PRD/spec
    documents) and log the outcome. Returns True on success, False on failure. Exits the
    process directly on an auth error or usage-limit hit, matching every other clarify
    subcommand's behavior."""
    prompt = build_apply_clarification_prompt(config)
    if not run_apply_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Apply stopped — usage limit reached.")
            sys.exit(2)
        log("Apply clarification failed.")
        return False
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Apply stopped — usage limit reached.")
        sys.exit(2)

    config = load_config()
    f = config.get("last_clarification_findings", {})
    log(f"Apply clarification done. Remaining findings: "
        f"critical={f.get('critical', 0)}, major={f.get('major', 0)}, minor={f.get('minor', 0)}")
    config["last_clarification_action"] = "apply"
    _record_clarify_applied_state(config, Path(get_sources(config).get("clarifications", "")))
    return True


def _ask_continue_clarification() -> bool:
    """After an apply step finishes (whether triggered from the web UI or via an explicit
    --apply), ask the user whether to run another clarification round right away. Only
    asked interactively; --finalize already loops by rule and never needs this prompt.
    Returns False (skipping the prompt entirely) when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("Run another clarification round now? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def run_clarify_apply() -> None:
    """Apply the answers/resolutions recorded in the clarification files to the PRD/spec
    documents (one session, WITHOUT re-evaluating). Prerequisite: clarification results
    must already exist — run clarify (and answer, manually or via --auto-answer) first."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    existing = _clarification_result_files(clar_dir)
    if not existing:
        log("No clarification results to apply yet. Run first: tempa clarify")
        sys.exit(0)

    _banner(f"Clarify (apply) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    success = _run_apply_step(config)
    if success and _ask_continue_clarification():
        log("Starting another clarification round...")
        run_clarify_once(noui=False)
        return
    sys.exit(0 if success else 1)


def _clarification_result_files(clar_dir: Path) -> list[Path]:
    """All clarification result .md files in `clar_dir`, excluding claude.md (case-
    insensitive), sorted by name for a stable, predictable tab order."""
    if not clar_dir.exists():
        return []
    return sorted(p for p in clar_dir.glob("*.md") if p.name.lower() != "claude.md")


def run_answer_command() -> None:
    """`tempa answer` — open the dashboard's Clarification section, without re-running
    clarify. Scans sources.clarifications for every clarification result file and — if at
    least one has an unanswered finding — opens the dashboard listing all such files in
    the left panel, so nothing unanswered is missed. If every file is already fully
    answered, reports that and does nothing."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    clar_dir = Path(clarifications_path)
    paths = _clarification_result_files(clar_dir)
    if not paths:
        log(f"No clarification files found in {clarifications_path}. Run first: tempa clarify")
        sys.exit(0)

    statuses = [file_answer_status(p) for p in paths]
    unanswered_files = sum(1 for answered, total in statuses if total > 0 and answered < total)
    if unanswered_files == 0:
        log("Every clarification file already has an answer for every finding — nothing left to answer.")
        sys.exit(0)

    log(f"Found {len(paths)} clarification file(s) in {clarifications_path} "
        f"({unanswered_files} with unanswered findings); opening the dashboard.")

    saved = run_dashboard(_resolve_prd_dir(config), clar_dir, initial_view="clarification")
    if saved:
        log("Answers saved. Run `tempa clarify --apply` when you're ready to apply them to the PRD/spec.")
    sys.exit(0)
