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

from dashboard_clarify_parse import (
    _file_started_at,
    file_answer_status,
    parse_file,
    pending_resolutions,
)
from dashboard_ui import run_dashboard
from tempa_backend import get_backend_def
from tempa_config import (
    get_backend,
    get_clarify_apply_session_id,
    get_clarify_session_id,
    get_finalize_no_progress_rounds,
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

# How many times one `clarify --finalize` run may rewrite the PRD/spec (see
# run_clarify_finalize). A compaction is followed by a verification round; if that round
# finds new critical/major issues the run answers them and compacts again — this is the
# bound that keeps that from turning into an unattended apply/evaluate ping-pong.
MAX_COMPACTIONS = 2


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


def _stamp_clean_evaluation_if_zero(config: dict, critical: int, major: int, minor: int) -> None:
    """If a fresh evaluate pass found truly zero findings (every severity), stamp
    config["last_clean_evaluation_at"] with the current time (caller still has to
    save_config). This covers the one case the file-based readiness gate
    (_latest_evaluation_findings in dashboard_clarify_parse.py) can't see on its own:
    per prompt/clarification.md the agent only writes a new clarification file when
    there's a finding to record, so a truly-clean round leaves no new file behind —
    the gate would otherwise keep reading whatever the last finding-bearing file
    said, even if that file is from an old round whose criticals/majors have since
    been resolved. Any round with even one remaining finding (of any severity) still
    gets its own file, so this only fires for the all-zero case."""
    if critical == 0 and major == 0 and minor == 0:
        config["last_clean_evaluation_at"] = time.time()


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


def _pending_overlay(config: dict, clar_dir: Path) -> list:
    """The pending-resolution overlay for an evaluate pass: every answered clarification
    finding whose answer isn't in the PRD yet (see pending_resolutions). Carrying these in
    the prompt is what lets clarification continue without an apply pass in between — the
    agent judges the spec as it will read once they're applied, instead of re-raising
    points that are already settled but not yet written down.

    Must be recomputed immediately before each evaluate: answering (or auto-answering) a
    finding grows the overlay, and applying empties it."""
    return pending_resolutions(clar_dir, config.get("clarify_applied_hashes", {}) or {})


def _log_pending_overlay(config: dict, clar_dir: Path) -> list:
    """_pending_overlay + a one-line note of what's being carried, so the session log shows
    why an evaluation prompt is bigger than the last one."""
    pending = _pending_overlay(config, clar_dir)
    if pending:
        rounds = len({p.round_index for p in pending})
        log(f"Carrying {len(pending)} already-decided resolution(s) from {rounds} unapplied "
            "round(s) into this evaluation.")
    return pending


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
    prompt = build_clarification_prompt(config, skip_minor, _log_pending_overlay(config, clar_dir))
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
    _stamp_clean_evaluation_if_zero(config, critical, major, minor)
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
        print("     Write the answers into the PRD/spec:  tempa clarify --apply  (required before implementing)", flush=True)
        print("     Then move on to the next stage:       tempa implement  (auto plan runs first)", flush=True)
    elif critical == 0:
        print(f"Only {major} major finding(s) remain (no critical). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Clarify again to check what's left:                     tempa clarify", flush=True)
        print("     (answers are carried into the next round — no need to apply first)", flush=True)
        print("  3. Write the answers into the PRD/spec:                    tempa clarify --apply", flush=True)
        print("  (Or do all of it unattended, evaluate+answer loop:         tempa clarify --finalize)", flush=True)
    else:
        print(f"[!] There are {critical} critical finding(s). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Repeat tempa clarify to verify — your answers are carried into the next round.", flush=True)
        print("     Applying them to the PRD/spec (tempa clarify --apply) can wait until you're", flush=True)
        print("     done clarifying; it's only required before implementing.", flush=True)

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
            log("Answers saved. They're carried into the next clarification round as already-decided "
                "resolutions; run `tempa clarify --apply` to write them into the PRD/spec (required "
                "before implementing).")

    sys.exit(0)


def _run_auto_answer_step(config: dict, unanswered_files: list[Path]) -> bool:
    """Run ONE auto-answer session over exactly `unanswered_files` (every file with at least
    one blank "Your answer") and log the outcome. Returns True on success, False on failure.
    Exits the process directly on an auth error, matching _run_apply_step and every other
    clarify step (a usage-limit hit is not a failure — it's retried in place; see
    run_with_usage_limit_retry).

    Uses the "clarify_apply" stage's backend/model/effort rather than "clarify"'s —
    auto-answer is mechanical work (pick/copy a resolution into the blank), the same
    reasoning as apply's (see DEFAULT_MODELS).

    Shared by `clarify --auto-answer` (run_clarify_answer, which reports the count and
    exits) and by the finalize loop (run_clarify_finalize, which answers between evaluate
    rounds), so both go through identical session/retry handling."""
    # Reset the marker so a stale value from a previous run isn't misread.
    config["last_auto_answer"] = 0
    save_config(config)

    prompt = build_auto_answer_prompt(config, unanswered_files)
    if not run_with_usage_limit_retry(
        lambda: run_clarification_session(
            prompt, 1, get_backend_def(get_backend(config, "clarify_apply")),
            get_model(config, "clarify_apply"), get_reasoning_effort(config, "clarify_apply"),
        ),
        "Auto-answer",
    ):
        if _state.auth_error_hit:
            sys.exit(3)
        log("Auto-answer failed.")
        return False
    if _state.auth_error_hit:
        sys.exit(3)
    return True


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

    # Only files with at least one unanswered finding need to be read/written by the
    # agent — files that are already fully answered add nothing (see
    # _clarification_backlog's unanswered_files, same split the dashboard uses).
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, _ = _clarification_backlog(clar_dir, applied_hashes)
    if not unanswered_files:
        print("[OK] Every clarification finding already has an answer — nothing left to answer.", flush=True)
        sys.exit(0)

    _banner(f"Clarify (auto-answer) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    start_ts = time.time() - 1
    if not _run_auto_answer_step(config, unanswered_files):
        sys.exit(1)

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
        if any(not it.resolved_answer for it in items):
            unanswered.append(p)
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if applied_hashes.get(p.name) != content_hash:
            unapplied.append(p)
    return unanswered, unapplied


def _fill_unanswered_with_recommendations(paths: list[Path]) -> int:
    """For every finding in `paths` that has no answer yet, mark it as "follow the
    recommendation" — mechanically, with no agent/LLM call — the same outcome the
    dashboard's "Follow the recommendation" button records for that finding: a
    `mode="recommendation"` marker with an EMPTY body, not a copy of the recommendation
    text (see ClarificationItem.resolved_answer, which reconstructs the full text for
    anything that reads "the answer"). Findings that already have an answer (per
    resolved_answer), or (unexpectedly) have no recommendation text, are left untouched.
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
            if it.resolved_answer or not it.recommendation:
                continue
            if it.has_markers:
                replacement = '<!-- clarify:answer-start mode="recommendation" -->\n\n<!-- clarify:answer-end -->'
            else:
                replacement = '\n<!-- clarify:answer-start mode="recommendation" -->\n\n<!-- clarify:answer-end -->\n'
            new_text = new_text[: it.answer_start] + replacement + new_text[it.answer_end:]
            filled += 1
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
    return filled


def _prepare_finalize_backlog(config: dict, clar_dir: Path) -> None:
    """Pre-flight step for `clarify --finalize` (and, by extension, the dashboard's
    Finalize button, which just spawns `tempa clarify --finalize` — see dashboard_runs.py):
    make sure everything already sitting in `sources.clarifications` from earlier manual or
    partial work carries an answer, so finalize can be started at any time regardless of
    backlog state instead of requiring it to be cleared by hand first.

    Findings with no answer yet are filled mechanically with their own Recommendation text
    (no agent call — the same "follow the recommendation" resolution the dashboard button
    writes). That's all: the backlog is NOT applied to the PRD here. Everything answered
    enters the loop as part of the pending overlay (see _pending_overlay), which every
    evaluate pass carries, and the loop compacts the whole lot into the PRD in one apply at
    the very end. A pre-loop apply would just be an extra full agent session writing text
    that the rounds after it are about to revise anyway.

    Nothing here can fail — there is no session to run — so unlike the apply-based version
    this replaced, the finalize run has no pre-loop failure path to handle."""
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, _ = _clarification_backlog(clar_dir, applied_hashes)
    if unanswered_files:
        _banner("Clarify (finalize) — answering pre-existing backlog before the loop starts")
        filled = _fill_unanswered_with_recommendations(unanswered_files)
        log(f"Filled {filled} unanswered finding(s) across {len(unanswered_files)} file(s) "
            "with their own recommendation (backlog pre-check).")

    pending = _pending_overlay(config, clar_dir)
    if pending:
        rounds = len({p.round_index for p in pending})
        log(f"Starting the finalize loop carrying {len(pending)} already-decided resolution(s) "
            f"from {rounds} unapplied round(s) into every evaluation.")
    else:
        log("No pre-existing clarification backlog — starting the finalize loop.")


def run_clarify_finalize(skip_minor: bool = False) -> None:
    """Unattended clarification: loop evaluate -> auto-answer until an evaluation reports no
    critical/major findings, then write every accumulated answer into the PRD/spec in ONE
    apply pass ("compaction"), then run one more evaluation to verify the updated documents.

    No apply runs inside the loop. Each evaluate carries every answer recorded so far as the
    pending overlay (_pending_overlay), so the agent judges the spec as it will read once
    those decisions are applied — which is what makes a per-round PRD rewrite unnecessary.
    One apply at the end replaces N of them.

    The closing verification round is not optional bookkeeping: apply is an agent rewriting
    prose, so the only way to know the PRD itself (with no overlay in front of it) is clean
    is to evaluate it again. It also leaves last_clarification_action == "evaluate", which is
    what _clarify_finalize_status requires to call the workspace ready.

    If that verification is NOT clean, the run falls back into the loop — auto-answering the
    new findings and compacting again — bounded by MAX_COMPACTIONS. Failing outright instead
    would leave a PRD that has been rewritten but never verified, which is strictly worse
    than the state finalize started from.

    `skip_minor` instructs every evaluate pass in the loop to skip minor findings entirely
    (config.json's "skip_minor_findings" / CLI --skip-minor)."""
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
    no_progress_limit = get_finalize_no_progress_rounds(config)
    run_number = 0
    no_progress_rounds = 0
    prev_total = None  # critical+major from the previous round, for the convergence guard
    compactions = 0
    # "evaluate" while findings are still being found and answered; "verify" for the round
    # that checks the PRD right after a compaction (same prompt, but with an empty overlay
    # in front of it, since applying just emptied it).
    phase = "evaluate"

    _banner(f"Clarify (finalize) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path} | max_runs={max_run}")

    _prepare_finalize_backlog(config, clar_dir)

    # Reset the finalize-only round counter to 0 for every fresh `clarify --finalize`
    # invocation — unlike last_clarification_round below (a running total across every
    # evaluate pass ever, manual or finalize), this one exists purely to show progress
    # against max_run for THIS run, so it always restarts from 0 rather than picking up
    # wherever a previous finalize run (or manual clarify) left off.
    config["last_finalize_round"] = 0
    save_config(config)

    while run_number < max_run:
        run_number += 1

        round_header = (f"ROUND {run_number}/{max_run} — {phase} — "
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        _banner(round_header)
        log(round_header, to_console=False)

        config = load_config()
        # Recomputed every round: auto-answering below grows the overlay, and a compaction
        # empties it (so the verify round below evaluates the PRD on its own merits, which
        # is the whole point of running it).
        prompt = build_clarification_prompt(config, skip_minor, _log_pending_overlay(config, clar_dir))

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
        _stamp_clean_evaluation_if_zero(config, critical, major, minor)
        config["last_clarification_action"] = "evaluate"
        # Running total across every evaluate pass ever (manual `clarify` or one iteration
        # of `clarify --finalize`) — NOT reset here, unlike last_finalize_round above, so
        # it keeps counting across finalize runs and manual runs alike.
        config["last_clarification_round"] = config.get("last_clarification_round", 0) + 1
        config["last_finalize_round"] = run_number
        config["last_finalize_phase"] = phase
        save_config(config)
        report_files = _clarification_report_files(clar_dir, start_ts)
        _stamp_clarify_timing(report_files, "clarify_seconds", time.time() - start_ts)

        log(f"Round #{run_number} ({phase}) findings: critical={critical}, major={major}, minor={minor}")

        if critical == 0 and major == 0:
            if phase == "verify":
                log("Verification round is clean — the PRD/spec documents now contain every "
                    "recorded resolution. Clarify (finalize) done.")
                if minor > 0:
                    log(f"Still {minor} minor finding(s) — considered acceptable.")
                sys.exit(0)

            # Clean evaluate: nothing new left to ask, so write the whole accumulated
            # overlay into the PRD in one pass, then verify what that pass produced.
            config = load_config()
            applied_hashes = config.get("clarify_applied_hashes", {}) or {}
            unanswered_files, unapplied_files = _clarification_backlog(clar_dir, applied_hashes)
            if not unanswered_files and not unapplied_files:
                log("No critical/major findings, and every recorded answer is already in the "
                    "PRD/spec. Clarify (finalize) done.")
                if minor > 0:
                    log(f"Still {minor} minor finding(s) — considered acceptable.")
                sys.exit(0)

            compactions += 1
            if compactions > MAX_COMPACTIONS:
                log(f"The PRD/spec has already been rewritten {MAX_COMPACTIONS} time(s) in this run "
                    "and the verification round keeps finding new critical/major issues — stopping "
                    "instead of rewriting it again. The remaining findings need a human decision "
                    "(see `tempa answer`).")
                notify_attention(
                    AttentionEventType.CLARIFICATION_ANSWERS_REQUIRED, "Clarification",
                    "Clarification needs human answers",
                    "Applying the answers keeps surfacing new findings — review them by hand.",
                    details={"compactions": compactions - 1},
                )
                sys.exit(1)

            log(f"No critical/major findings remain — writing {len(unanswered_files) + len(unapplied_files)} "
                f"pending file(s) of resolutions into the PRD/spec documents "
                f"(compaction {compactions}/{MAX_COMPACTIONS})...")
            # Resume the evaluate session that just ran (see tempa_config.get_clarify_session_id /
            # run_clarification_session) — it already paid to read the whole PRD AND was handed
            # the entire overlay, which is exactly what this apply has to write, so resuming
            # reuses that context instead of a cold session re-reading everything itself.
            # Checked against the "clarify_apply" stage's backend (not "clarify"'s) — that's
            # the CLI that will actually try the --resume, and a session id only means
            # anything to the backend that produced it. If clarify_apply is configured with a
            # different backend than clarify (e.g. evaluate on Claude, apply on Codex), there
            # is nothing to resume and this correctly returns None.
            resume_sid = get_clarify_session_id(config, get_backend(config, "clarify_apply"))
            if not _run_apply_step(config, resume_session_id=resume_sid):
                log(f"Apply-clarification run #{run_number} failed — stopping the loop.")
                notify_attention(
                    AttentionEventType.CLARIFICATION_FAILED, "Clarification",
                    f"Clarification apply round {run_number} failed",
                    "Review the apply session log and resolve the failure before continuing.",
                    details={"round": run_number},
                )
                sys.exit(1)

            # The apply just rewrote the PRD, so a finding count from before it says nothing
            # about whether the loop is still making progress — comparing the verify round
            # against it would trip the convergence guard on the very first verification.
            prev_total = None
            no_progress_rounds = 0
            phase = "verify"
            log("Resolutions written into the PRD/spec. Running a verification evaluation "
                "against the updated documents...")
            continue

        # Convergence guard: if `no_progress_limit` rounds in a row fail to reduce the
        # critical+major count, evaluate+auto-answer has run out of resolutions it can make
        # on its own (e.g. every remaining finding genuinely needs a human decision) — stop
        # instead of burning up to max_clarification_run rounds of full-PRD re-evaluation
        # for no benefit.
        total = critical + major
        if prev_total is not None and total >= prev_total:
            no_progress_rounds += 1
        else:
            no_progress_rounds = 0
        prev_total = total
        if no_progress_rounds >= no_progress_limit:
            log(f"No reduction in critical/major findings for {no_progress_rounds} round(s) in a row "
                f"— stopping instead of continuing to {max_run} rounds. {critical} critical and {major} "
                "major finding(s) remain and likely need a human decision (see `tempa answer`).")
            notify_attention(
                AttentionEventType.CLARIFICATION_ANSWERS_REQUIRED, "Clarification",
                "Clarification needs human answers",
                "Review and answer the remaining findings before continuing finalization.",
                details={"critical": critical, "major": major},
            )
            sys.exit(1)

        # Answer what this round found — never apply here. The answers join the overlay and
        # are carried into the next evaluation; the PRD isn't touched until the compaction.
        config = load_config()
        applied_hashes = config.get("clarify_applied_hashes", {}) or {}
        unanswered_files, _ = _clarification_backlog(clar_dir, applied_hashes)
        if unanswered_files:
            log(f"Still {critical} critical and {major} major finding(s) — answering "
                f"{len(unanswered_files)} file(s) with unanswered findings...")
            if not _run_auto_answer_step(config, unanswered_files):
                log(f"Auto-answer run #{run_number} failed — stopping the loop.")
                notify_attention(
                    AttentionEventType.CLARIFICATION_FAILED, "Clarification",
                    f"Clarification auto-answer round {run_number} failed",
                    "Review the auto-answer session log and resolve the failure before continuing.",
                    details={"round": run_number},
                )
                sys.exit(1)
            # Backstop: the agent may leave a blank behind. The overlay has to be complete or
            # the next evaluation simply re-raises the finding and the loop can't converge.
            filled = _fill_unanswered_with_recommendations(unanswered_files)
            if filled:
                log(f"Filled {filled} finding(s) the auto-answer pass left blank with their own "
                    "recommendation.")
        else:
            log(f"The evaluation reported {critical} critical and {major} major finding(s) but left "
                "no unanswered finding behind — nothing to auto-answer this round.")
        phase = "evaluate"

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


def _run_apply_step(config: dict, resume_session_id: str | None = None) -> bool:
    """Run one apply-clarification session (writes answers/resolutions into the PRD/spec
    documents) and log the outcome. Returns True on success, False on failure. Exits the
    process directly on an auth error, matching every other clarify subcommand's behavior
    (a usage-limit hit is not a failure — it's retried in place; see
    run_with_usage_limit_retry).

    Only the apply backlog (files not yet reflected in config["clarify_applied_hashes"])
    is sent to the agent — see _clarification_backlog — instead of every clarification
    file ever written, so an apply session's input doesn't grow with the number of past
    clarification rounds. If there's no backlog at all (e.g. called right after a fresh
    evaluate pass with nothing to apply yet, or everything was already applied), no
    session is spawned.

    `resume_session_id`, when given, is passed straight through to
    run_apply_clarification_session — normally the evaluate session that just wrote the
    backlog files (see run_clarify_finalize), so this apply pass reuses that session's
    already-paid-for PRD context instead of starting cold. Callers that apply independently
    of any evaluate pass in this process (e.g. a standalone `tempa clarify --apply`) leave
    this None, which is the previous, always-fresh behavior."""
    clar_dir = Path(get_sources(config).get("clarifications", ""))
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, unapplied_files = _clarification_backlog(clar_dir, applied_hashes)
    # Oldest round first: prompt/apply_clarification.md tells the agent that a later file's
    # decision supersedes an earlier one covering the same point, which only holds if the
    # list is chronological. Filenames sort that way by convention, not by guarantee — sort
    # on the parsed round timestamp (see _file_started_at) and keep the name as tie-break.
    backlog = sorted(set(unanswered_files) | set(unapplied_files),
                     key=lambda p: (_file_started_at(p), p.name))
    if not backlog:
        log("Apply: nothing to apply — every clarification file is already applied.")
        return True

    # Mechanically fill any still-empty "Your answer" with its own Recommendation text
    # BEFORE applying (no agent call — same as _prepare_finalize_backlog's pre-loop
    # step) so the clarification file itself ends up recording exactly what the apply
    # prompt is about to do (fall back to Recommendation for anything left unanswered).
    # Without this, a finding resolved this way stays permanently "Unanswered" in the
    # dashboard even after its resolution has been successfully applied to the PRD/spec.
    if unanswered_files:
        filled = _fill_unanswered_with_recommendations(unanswered_files)
        if filled:
            log(f"Filled {filled} unanswered finding(s) across {len(unanswered_files)} file(s) "
                "with their own recommendation before applying.")

    prompt = build_apply_clarification_prompt(config, backlog)
    apply_start_ts = time.time()
    apply_backend = get_backend_def(get_backend(config, "clarify_apply"))
    apply_model = get_model(config, "clarify_apply")
    apply_effort = get_reasoning_effort(config, "clarify_apply")

    def _attempt_apply() -> bool:
        # Re-read fresh on every attempt (including retries): prefer resuming THIS apply
        # step's own previous (usage-limit/overload-interrupted) attempt if one exists —
        # it may already have made partial progress — otherwise fall back to
        # `resume_session_id` (typically the evaluate session that just wrote this
        # backlog, passed in by the caller).
        cfg = load_config()
        sid = get_clarify_apply_session_id(cfg, apply_backend.name) or resume_session_id
        return run_apply_clarification_session(prompt, 1, apply_backend, apply_model, apply_effort, resume_session_id=sid)

    if not run_with_usage_limit_retry(_attempt_apply, "Apply"):
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
    # This apply step is done — drop its retry-resume id so a LATER, unrelated apply call
    # doesn't try to resume a session that already finished this backlog.
    config.pop("clarify_apply_session_id", None)
    config.pop("clarify_apply_session_backend", None)
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
        log("Answers saved. They're carried into the next clarification round as already-decided "
            "resolutions; run `tempa clarify --apply` to write them into the PRD/spec (required "
            "before implementing).")
    sys.exit(0)
