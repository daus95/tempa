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

from dashboard_clarify_parse import file_answer_status, parse_file
from dashboard_ui import run_dashboard
from tempa_backend import get_backend_def
from tempa_config import (
    get_backend,
    get_model,
    get_reasoning_effort,
    get_sources,
    load_config,
    save_config,
)
from tempa_config import resolve_prd_dir as _resolve_prd_dir
from tempa_logging import _banner, _hyperlink, _init_process_log, _state, log
from tempa_notifications import AttentionEventType, flush_pending_notifications, notify_attention
from tempa_prompts import (
    build_apply_clarification_prompt,
    build_auto_answer_prompt,
    build_clarification_prompt,
)
from tempa_session import (
    run_apply_clarification_session,
    run_clarification_session,
    run_with_usage_limit_retry,
)


def _stamp_clarify_timing(filenames: list[Path], key: str, seconds: float) -> None:
    """Merge a per-file clarify/apply duration into config.json's
    "clarify_file_timings" ({filename: {"clarify_seconds": ..., "apply_seconds": ...}}),
    surfaced by the dashboard's clarification-row detail modal. `key` is
    "clarify_seconds" (how long the evaluate session that produced the file took) or
    "apply_seconds" (how long the apply session that most recently covered the file
    took — apply always reads every existing clarification file, not just the one(s)
    it changed, so every file present at apply time gets stamped). Reads config fresh
    right before writing so a concurrent update (e.g. the agent session's own
    last_clarification_findings write) isn't clobbered."""
    if not filenames:
        return
    config = load_config()
    timings = config.get("clarify_file_timings")
    if not isinstance(timings, dict):
        timings = {}
    for f in filenames:
        entry = dict(timings.get(f.name) or {})
        entry[key] = round(seconds, 1)
        timings[f.name] = entry
    config["clarify_file_timings"] = timings
    save_config(config)


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


def run_clarify_once(noui: bool = False, skip_minor: bool = False) -> None:
    """Manual clarification — run ONE evaluation pass, report findings + report file(s),
    then suggest the next step based on severity:
      - critical==0 & major==0 : clarification done → suggest moving on to implement (auto plan)
      - critical==0 (major>0)  : suggest answering manually, or finishing with clarify --finalize
      - critical>0             : suggest reviewing/answering manually then clarify again
    Unless `noui` is set, also opens the clarification-answer web UI on the freshly
    written report file(s) so the user can answer right away instead of hand-editing
    the markdown. `skip_minor` instructs the evaluation pass to skip minor findings
    entirely (config.json's "skip_minor_findings" / CLI --skip-minor)."""
    _init_process_log()
    flush_pending_notifications()

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
    prompt = build_clarification_prompt(config, skip_minor)
    if not run_with_usage_limit_retry(
        lambda: run_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify"), get_reasoning_effort(config, "clarify")),
        "Clarification evaluation",
    ):
        if _state.auth_error_hit:
            sys.exit(3)
        log("Clarification evaluation failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)

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
    _stamp_clarify_timing(report_files, "clarify_seconds", time.time() - start_ts)

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

    if critical or major:
        notify_attention(
            AttentionEventType.CLARIFICATION_ANSWERS_REQUIRED,
            "Clarification", "Clarification answers are required",
            "Review and answer the reported critical/major findings, then apply the answers.",
            log_path=report_files[0] if report_files else None,
            details={"critical": critical, "major": major},
        )

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
    flush_pending_notifications()

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
    if not run_with_usage_limit_retry(
        lambda: run_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify"), get_reasoning_effort(config, "clarify")),
        "Auto-answer",
    ):
        if _state.auth_error_hit:
            sys.exit(3)
        log("Auto-answer failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)

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


def _clarification_backlog(clar_dir: Path, applied_hashes: dict) -> tuple[list[Path], list[Path]]:
    """Split every existing clarification result file into (unanswered_files,
    unapplied_answered_files), mirroring the dashboard's Clarification Overview split
    (see _clarify_files_overview in dashboard_clarify_parse.py) but returning bare
    Paths instead of display dicts:
      - unanswered_files: at least one finding in the file has no "Your answer" yet.
      - unapplied_answered_files: every finding is answered, but the file's current
        content hash doesn't match `applied_hashes` (config.json's
        "clarify_applied_hashes") — i.e. an apply pass hasn't picked up this exact
        content yet (either never applied, or edited since the last apply).
    A file that's fully answered AND already applied appears in neither list."""
    unanswered: list[Path] = []
    unapplied: list[Path] = []
    for p in _clarification_result_files(clar_dir):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        items, _ = parse_file(p, text, 0)
        if not items:
            continue
        if any(not it.existing_answer for it in items):
            unanswered.append(p)
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if applied_hashes.get(p.name) != content_hash:
            unapplied.append(p)
    return unanswered, unapplied


def _fill_unanswered_with_recommendations(paths: list[Path]) -> int:
    """For every finding in `paths` that has no answer yet, write its own
    Recommendation text verbatim into the "Your answer" section — mechanically,
    with no agent/LLM call — the same content the dashboard's "Follow the
    recommendation" button would save for that finding. Findings that already have
    an answer, or (unexpectedly) have no recommendation text, are left untouched.
    Returns how many findings were filled in."""
    filled = 0
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        items, _ = parse_file(p, text, 0)
        new_text = text
        # Rewrite back-to-front so each item's recorded (answer_start, answer_end)
        # offsets — computed against the original text — stay valid for the items
        # processed after it.
        for it in sorted(items, key=lambda i: i.answer_start, reverse=True):
            if it.existing_answer or not it.recommendation:
                continue
            if it.has_markers:
                replacement = f"<!-- clarify:answer-start -->\n{it.recommendation}\n<!-- clarify:answer-end -->"
            else:
                replacement = it.recommendation
            new_text = new_text[: it.answer_start] + replacement + new_text[it.answer_end:]
            filled += 1
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
    return filled


def _resolve_clarification_backlog(config: dict, clar_dir: Path) -> bool:
    """Pre-flight step for `clarify --finalize` (and, by extension, the dashboard's
    Finalize button, which just spawns `tempa clarify --finalize` — see
    dashboard_runs.py): before finalize's own evaluate/apply loop starts, resolve
    whatever's already sitting in `sources.clarifications` from earlier manual/
    partial work, so finalize can be run at any time regardless of backlog state
    instead of requiring it to be cleared by hand first:
      - files with every finding answered but not yet applied -> just need applying
      - files with at least one unanswered finding -> answer it by mechanically
        copying that finding's own Recommendation text (no agent call — the
        "follow recommendation" resolution), THEN apply
    A single `clarify --apply` pass (an agent session that reads every clarification
    file under sources.clarifications and writes their recorded answers into the
    PRD/spec) covers both cases at once, since it always processes the whole
    folder — there's no need to apply the already-answered files and the
    freshly-recommendation-filled files separately.
    Returns True if the loop below is clear to start (either there was no backlog,
    or the backlog was resolved successfully); False if an apply attempt failed and
    the whole finalize run should stop (mirrors every other failure path in this
    module — the caller still exits non-zero)."""
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, unapplied_files = _clarification_backlog(clar_dir, applied_hashes)
    if not unanswered_files and not unapplied_files:
        log("No pre-existing unanswered/unapplied clarification backlog — starting the finalize loop.")
        return True

    _banner("Clarify (finalize) — resolving pre-existing backlog before the loop starts")
    if unanswered_files:
        filled = _fill_unanswered_with_recommendations(unanswered_files)
        log(f"Filled {filled} unanswered finding(s) across {len(unanswered_files)} file(s) "
            "with their own recommendation (backlog pre-check).")
    total_backlog = len(unanswered_files) + len(unapplied_files)
    log(f"Applying {total_backlog} backlog file(s) to the PRD/spec before the finalize loop starts...")
    config = load_config()
    if not _run_apply_step(config):
        log("Backlog apply failed — stopping before the finalize loop.")
        return False
    log("Backlog resolved. Starting the finalize loop.")
    return True


def run_clarify_finalize(skip_minor: bool = False) -> None:
    """`skip_minor` instructs every evaluate pass in the loop to skip minor findings
    entirely (config.json's "skip_minor_findings" / CLI --skip-minor)."""
    _init_process_log()
    flush_pending_notifications()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")

    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    clar_dir = Path(clarifications_path)
    clar_dir.mkdir(parents=True, exist_ok=True)

    max_run = config.get("max_clarification_run", 20)
    run_number = 0

    _banner(f"Clarify (finalize) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path} | max_runs={max_run}")

    if not _resolve_clarification_backlog(config, clar_dir):
        sys.exit(1)

    while run_number < max_run:
        run_number += 1

        round_header = f"ROUND {run_number}/{max_run} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        _banner(round_header)
        log(round_header, to_console=False)

        config = load_config()
        prompt = build_clarification_prompt(config, skip_minor)

        start_ts = time.time() - 1  # small epsilon so freshly-written files are caught
        # Retry's lambda binds the loop variables as defaults (not by closure) so a retry
        # can't accidentally pick up a later iteration's prompt/run_number/config.
        success = run_with_usage_limit_retry(
            lambda prompt=prompt, run_number=run_number, config=config: run_clarification_session(
                prompt, run_number, get_backend_def(get_backend(config, "clarify")),
                get_model(config, "clarify"), get_reasoning_effort(config, "clarify"),
            ),
            f"Clarify (finalize) round #{run_number} — evaluate",
        )
        if _state.auth_error_hit:
            sys.exit(3)
        if not success:
            log(f"Clarification run #{run_number} failed — stopping the loop.")
            notify_attention(
                AttentionEventType.CLARIFICATION_FAILED, "Clarification",
                f"Clarification round {run_number} failed",
                "Review the clarification session log and resolve the failure before continuing.",
            )
            sys.exit(1)

        config = load_config()
        findings = config.get("last_clarification_findings", {})
        critical = findings.get("critical", 0)
        major = findings.get("major", 0)
        minor = findings.get("minor", 0)
        config["last_clarification_action"] = "evaluate"
        config["last_clarification_round"] = run_number
        save_config(config)
        report_files = _clarification_report_files(clar_dir, start_ts)
        _stamp_clarify_timing(report_files, "clarify_seconds", time.time() - start_ts)

        log(f"Round #{run_number} findings: critical={critical}, major={major}, minor={minor}")

        if critical == 0 and major == 0:
            log("No critical/major findings. Clarify (finalize) done.")
            if minor > 0:
                log(f"Still {minor} minor finding(s) — considered acceptable.")
            sys.exit(0)

        log(f"Still {critical} critical and {major} major findings remain — applying resolutions to the PRD/spec documents...")

        config = load_config()
        apply_prompt = build_apply_clarification_prompt(config)
        apply_start_ts = time.time()
        apply_success = run_with_usage_limit_retry(
            lambda apply_prompt=apply_prompt, run_number=run_number, config=config: run_apply_clarification_session(
                apply_prompt, run_number, get_backend_def(get_backend(config, "clarify")),
                get_model(config, "clarify"), get_reasoning_effort(config, "clarify"),
            ),
            f"Clarify (finalize) round #{run_number} — apply",
        )
        if _state.auth_error_hit:
            sys.exit(3)
        if not apply_success:
            log(f"Apply-clarification run #{run_number} failed — stopping the loop.")
            notify_attention(
                AttentionEventType.CLARIFICATION_FAILED, "Clarification",
                f"Clarification apply round {run_number} failed",
                "Review the apply session log and resolve the failure before continuing.",
            )
            sys.exit(1)

        config = load_config()
        config["last_clarification_action"] = "apply"
        save_config(config)
        _stamp_clarify_timing(_clarification_result_files(clar_dir), "apply_seconds", time.time() - apply_start_ts)

        log("Resolutions applied. Running re-evaluation...")

    log(f"Clarify (finalize) reached the {max_run}-run limit. Stopping.")
    notify_attention(
        AttentionEventType.CLARIFICATION_LIMIT_REACHED, "Clarification",
        "Finalized clarification reached its run limit",
        "Review the remaining findings and resolve them manually before running another finalization pass.",
        details={"max_clarification_run": max_run},
    )
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
    process directly on an auth error, matching every other clarify subcommand's behavior
    (a usage-limit hit is not a failure — it's retried in place; see
    run_with_usage_limit_retry)."""
    prompt = build_apply_clarification_prompt(config)
    apply_start_ts = time.time()
    if not run_with_usage_limit_retry(
        lambda: run_apply_clarification_session(prompt, 1, get_backend_def(get_backend(config, "clarify")), get_model(config, "clarify"), get_reasoning_effort(config, "clarify")),
        "Apply",
    ):
        if _state.auth_error_hit:
            sys.exit(3)
        log("Apply clarification failed.")
        return False
    if _state.auth_error_hit:
        sys.exit(3)

    config = load_config()
    f = config.get("last_clarification_findings", {})
    log(f"Apply clarification done. Remaining findings: "
        f"critical={f.get('critical', 0)}, major={f.get('major', 0)}, minor={f.get('minor', 0)}")
    config["last_clarification_action"] = "apply"
    clar_dir = Path(get_sources(config).get("clarifications", ""))
    _record_clarify_applied_state(config, clar_dir)
    _stamp_clarify_timing(_clarification_result_files(clar_dir), "apply_seconds", time.time() - apply_start_ts)
    return True


def _ask_continue_clarification() -> bool:
    """After an apply step finishes (whether triggered from the web UI or via an explicit
    --apply), ask the user whether to run another clarification round right away. Only
    asked interactively; --finalize already loops by rule and never needs this prompt.
    Returns False (skipping the prompt entirely) when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return False
    notify_attention(
        AttentionEventType.CONFIRMATION_REQUIRED, "Clarification",
        "Clarification is waiting for confirmation",
        "Return to the terminal and answer whether to run another clarification round.",
    )
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
