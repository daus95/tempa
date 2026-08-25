"""The clarify workflow: evaluate the PRD for ambiguities, answer, and apply.

One-pass evaluate (`run_clarify_once`), auto-answer (`run_clarify_answer`), apply resolutions
to the PRD (`run_clarify_apply`), the evaluate+apply loop (`run_clarify_finalize`), and opening
the answer dashboard (`run_answer_command`). Session running lives in tempa_session; prompt
construction in tempa_prompts; this module orchestrates them and interprets config.json's
last_clarification_* state.
"""

from __future__ import annotations

import hashlib
import re
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
    clear_graceful_stop,
    get_backend,
    get_clarify_apply_session_id,
    get_clarify_session_id,
    get_clarify_severity_phases,
    get_critical_phase_max_rounds,
    get_finalize_checkpoint_commit,
    get_finalize_checkpoint_rounds,
    get_finalize_no_progress_rounds,
    get_model,
    get_reasoning_effort,
    get_sources,
    get_workspace,
    graceful_stop_requested,
    load_config,
    save_config,
)
from tempa_config import resolve_prd_dir as _resolve_prd_dir
from tempa_git import commit_workspace_changes, ensure_prd_tracked
from tempa_logging import _banner, _hyperlink, _init_process_log, _state, log
from tempa_notifications import AttentionEventType, flush_pending_notifications, notify_attention
from tempa_prompts import (
    build_apply_clarification_prompt,
    build_auto_answer_prompt,
    build_clarification_prompt,
)
from tempa_session import run_with_usage_limit_retry
from tempa_session_runners import run_apply_clarification_session, run_clarification_session

# How many times one `clarify --finalize` run may rewrite the PRD/spec (see
# run_clarify_finalize). A compaction is followed by a verification round; if that round
# finds new critical/major issues the run answers them and compacts again — this is the
# bound that keeps that from turning into an unattended apply/evaluate ping-pong.
#
# Periodic checkpoint applies (_run_checkpoint) deliberately do NOT count against this. They
# are scheduled by a round counter rather than triggered by a verification coming back dirty,
# so they measure a different thing entirely — and charging them here would mean a run with
# checkpoints every 5 rounds could never reach its 20th round.
MAX_COMPACTIONS = 2

# The severity phases, in the order clarification walks them. The point of walking them at
# all is that a round can only ANSWER what it found: answering a major rewrites the spec, and
# a rewritten spec grows new criticals (see the overlay's rule 3c in prompt/clarification.md),
# so looking for majors while criticals are still being swept means sweeping a moving target.
_PHASE_CRITICAL = "critical"
_PHASE_MAJOR = "major"
_PHASE_MINOR = "minor"

# What each phase scopes its evaluation to (tempa_prompts.SEVERITY_SCOPES), and which
# severities have to reach zero before it is settled. Each phase WIDENS the scope by one
# severity rather than switching to it, so a later phase never stops checking what an earlier
# one established — which is what makes the demotion in run_clarify_finalize possible.
_PHASE_SCOPES = {
    _PHASE_CRITICAL: "critical",
    _PHASE_MAJOR: "critical_major",
    _PHASE_MINOR: "all",
}
_PHASE_BLOCKING = {
    _PHASE_CRITICAL: ("critical",),
    _PHASE_MAJOR: ("critical", "major"),
    _PHASE_MINOR: ("critical", "major", "minor"),
}

# Clean rounds in a row that settle a phase when no coverage ledger backs the result up.
# See _phase_may_advance for why there is a fallback at all.
_PHASE_CLEAN_ROUNDS_WITHOUT_LEDGER = 2

# The coverage ledger lives in a SUBFOLDER of sources.clarifications, never beside the
# findings files. Everything that reads that folder — _clarification_result_files here,
# clarification_files/pending_resolutions in dashboard_clarify_parse — globs "*.md"
# non-recursively and treats every hit as a findings file, so a ledger written flat would be
# parsed as a round, tabbed into the answer UI, and swept into the apply backlog.
_COVERAGE_DIRNAME = "coverage"

_COVERAGE_SUMMARY_RE = re.compile(r"<!--\s*coverage:summary\s+([^>]*?)-->", re.IGNORECASE)
_COVERAGE_ATTR_RE = re.compile(r'(?P<key>[a-z_]+)\s*=\s*"(?P<value>-?\d+)"', re.IGNORECASE)


def _phase_scope(phase: str, phases_on: bool, skip_minor: bool) -> str:
    """The severity scope one round of `phase` evaluates at.

    With phases off there is only ever one phase (_PHASE_MAJOR) and its scope is the
    pre-phases derivation from skip_minor — which is what keeps a phases-off run behaving
    exactly as clarification did before phases existed."""
    if not phases_on:
        return "critical_major" if skip_minor else "all"
    return _PHASE_SCOPES[phase]


def _phase_blocking_count(phase: str, critical: int, major: int, minor: int) -> int:
    """How many findings still stand between `phase` and being settled — the severities that
    phase is responsible for, and only those. A critical-only round reports major=0 because
    it never looked, so counting majors here would settle the critical phase on the strength
    of a number nobody measured."""
    counts = {"critical": critical, "major": major, "minor": minor}
    return sum(counts[sev] for sev in _PHASE_BLOCKING[phase])


def _next_phase(phase: str, phases_on: bool, skip_minor: bool) -> str | None:
    """The phase to enter once `phase` is settled, or None when clarification is finished.

    With phases off, _PHASE_MAJOR is the only phase and nothing follows it — the run ends
    where it always did, on zero critical and zero major. A minor phase exists only when
    minor findings are being looked for at all (skip_minor off); it sweeps them rather than
    gating on them, which is why nothing follows it either."""
    if not phases_on:
        return None
    if phase == _PHASE_CRITICAL:
        return _PHASE_MAJOR
    if phase == _PHASE_MAJOR and not skip_minor:
        return _PHASE_MINOR
    return None


def _phase_may_advance(blocking: int, coverage: dict | None, clean_rounds: int,
                       phases_on: bool = True) -> bool:
    """Whether a phase's sweep is finished, given this round's blocking-severity count, the
    coverage ledger that round produced, and how many clean rounds in a row it has now had
    (this one included).

    With phases off there is nothing to confirm: one clean round ended the run before phases
    existed and still does, so the ledger is written and logged but never gates anything.

    One clean round is not proof. The behavior this mechanism exists to fix had a round report
    zero criticals while three of them sat in the spec, so "the agent says it is clean" cannot
    be the thing that closes a phase. What replaces it is the ledger: a full check table with
    no unchecked row is checkable evidence the sweep was exhaustive, and one clean round
    carrying one is enough.

    Without a readable ledger there is no evidence, so the fallback is the crude one — two
    clean rounds in a row. A fallback rather than a hard requirement on purpose: the ledger is
    written by an agent, and a phase that could never advance because the agent kept omitting
    a marker would be a worse failure than the miss this guards against."""
    if blocking:
        return False
    if not phases_on:
        return True
    if coverage is not None and coverage.get("unchecked") == 0:
        return True
    return clean_rounds >= _PHASE_CLEAN_ROUNDS_WITHOUT_LEDGER


def _load_phase_state(config: dict, phases_on: bool) -> tuple[str, int]:
    """The severity phase clarification is on and how many clean rounds in a row it has had,
    as left behind by the last round — manual or finalize alike.

    Persisted rather than derived because manual clarification is one round per process:
    "still sweeping for criticals" has to survive the process exiting, or every
    `tempa clarify` would restart the sweep at the widest scope, which is exactly the behavior
    the phases exist to replace."""
    if not phases_on:
        return _PHASE_MAJOR, 0
    phase = config.get("last_severity_phase")
    if phase not in _PHASE_SCOPES:
        phase = _PHASE_CRITICAL
    clean = config.get("clarify_phase_clean_rounds", 0)
    valid = isinstance(clean, int) and not isinstance(clean, bool) and clean >= 0
    return phase, clean if valid else 0


def _save_phase_state(config: dict, phase: str, clean_rounds: int) -> None:
    """Record the phase state on `config` — the caller saves it, as everything else here does."""
    config["last_severity_phase"] = phase
    config["clarify_phase_clean_rounds"] = clean_rounds


def _coverage_dir(clar_dir: Path) -> Path:
    return clar_dir / _COVERAGE_DIRNAME


def _latest_coverage_ledger(clar_dir: Path) -> tuple[str, str] | None:
    """(file_name, text) of the most recent coverage ledger, or None if there is none.

    Newest by NAME, not mtime: the prompt names each ledger coverage-<YYYYMMDD-HHMMSS>.md so
    the names sort chronologically, and mtime would pick the wrong one the moment anything
    else touched an older file — a git checkout, an editor, a restored backup."""
    folder = _coverage_dir(clar_dir)
    if not folder.exists():
        return None
    files = sorted(p for p in folder.glob("coverage-*.md") if p.is_file())
    if not files:
        return None
    try:
        return files[-1].name, files[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse_coverage_summary(body: str) -> dict[str, int] | None:
    """The counts in a ledger's closing `<!-- coverage:summary ... -->` marker, or None when
    there is no such marker (or nothing numeric inside it). The LAST marker wins: the prompt
    asks for it as the file's last line, so an earlier one is the template being quoted."""
    matches = _COVERAGE_SUMMARY_RE.findall(body)
    if not matches:
        return None
    counts = {m.group("key").lower(): int(m.group("value"))
              for m in _COVERAGE_ATTR_RE.finditer(matches[-1])}
    return counts or None


def _round_coverage(clar_dir: Path, carried: tuple[str, str] | None) -> dict[str, int] | None:
    """The coverage summary of the ledger the evaluate session that just ran wrote, logged as
    it is read. None when that session wrote no ledger, or wrote one with no readable marker —
    two cases the caller treats identically, because they mean the same thing: this round
    produced no checkable evidence that its sweep was complete.

    `carried` is the ledger handed TO that session, so a ledger with the same file name is the
    old one still sitting there rather than a new one."""
    latest = _latest_coverage_ledger(clar_dir)
    if latest is None or (carried is not None and latest[0] == carried[0]):
        log("This round wrote no coverage ledger, so there is no record of what its critical "
            "sweep actually checked — a clean result can't be confirmed from it.")
        return None
    summary = _parse_coverage_summary(latest[1])
    if summary is None:
        log(f"Coverage ledger {latest[0]} has no readable coverage:summary marker.")
        return None
    log(f"Coverage ledger {latest[0]}: "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    return summary


def _build_evaluate_prompt(config: dict, clar_dir: Path, skip_minor: bool,
                           severity_scope: str) -> tuple[str, tuple[str, str] | None]:
    """The prompt for one evaluate pass, plus the coverage ledger it was handed — which the
    caller needs afterwards to tell the ledger this round writes apart from the one it carried
    in (see _round_coverage)."""
    coverage_dir = _coverage_dir(clar_dir)
    coverage_dir.mkdir(parents=True, exist_ok=True)
    carried = _latest_coverage_ledger(clar_dir)
    if carried:
        log(f"Carrying the previous coverage ledger ({carried[0]}) into this evaluation.")
    prompt = build_clarification_prompt(
        config, skip_minor, _log_pending_overlay(config, clar_dir),
        severity_scope=severity_scope, coverage_dir=str(coverage_dir), previous_ledger=carried)
    return prompt, carried


def _advance_phase(phase: str, phases_on: bool, skip_minor: bool, critical: int, blocking: int,
                   coverage: dict | None, clean_rounds: int) -> tuple[str, int, str]:
    """Where the phase machine goes after a round that reported `critical` critical findings
    and `blocking` findings in the phase's own severities. Shared by manual clarification and
    the finalize loop so the two can never drift into disagreeing about what settles a phase.

    Returns (phase, clean_rounds, transition), where transition is one of:

      "demoted"  — a later phase turned up a critical, so the next round narrows back to
                   criticals only. The spec moved under the sweep, which is precisely what
                   answering majors does and precisely what the phases exist to re-sweep.
      "advanced" — this phase is settled; the next one starts.
      "done"     — settled, with nothing after it: clarification is finished.
      "stay"     — still sweeping this phase, either because findings remain or because a
                   clean round isn't confirmed yet (see _phase_may_advance).
    """
    clean_rounds = 0 if blocking else clean_rounds + 1
    if phases_on and phase != _PHASE_CRITICAL and critical:
        return _PHASE_CRITICAL, 0, "demoted"
    if not _phase_may_advance(blocking, coverage, clean_rounds, phases_on):
        return phase, clean_rounds, "stay"
    following = _next_phase(phase, phases_on, skip_minor)
    if following is None:
        return phase, clean_rounds, "done"
    return following, 0, "advanced"


def _phase_label(phase: str) -> str:
    """How a phase is named in console output and logs."""
    return {_PHASE_CRITICAL: "critical sweep",
            _PHASE_MAJOR: "major sweep",
            _PHASE_MINOR: "minor sweep"}[phase]


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


def _stamp_clean_evaluation_if_zero(config: dict, critical: int, major: int, minor: int,
                                    severity_scope: str = "all") -> None:
    """If a fresh evaluate pass found truly zero findings (every severity), stamp
    config["last_clean_evaluation_at"] with the current time (caller still has to
    save_config). This covers the one case the file-based readiness gate
    (_latest_evaluation_findings in dashboard_clarify_parse.py) can't see on its own:
    per prompt/clarification.md the agent only writes a new clarification file when
    there's a finding to record, so a truly-clean round leaves no new file behind —
    the gate would otherwise keep reading whatever the last finding-bearing file
    said, even if that file is from an old round whose criticals/majors have since
    been resolved. Any round with even one remaining finding (of any severity) still
    gets its own file, so this only fires for the all-zero case.

    Only a round that actually looked for every severity may stamp it. A critical-only round
    (`severity_scope` "critical", the critical phase — see _PHASE_SCOPES) reports major=0 and
    minor=0 because it never looked for them, not because there are none; stamping on that
    would zero out the readiness gate's findings and open Start Implementation on a spec
    whose majors have never been evaluated."""
    if severity_scope != "all":
        return
    if critical == 0 and major == 0 and minor == 0:
        config["last_clean_evaluation_at"] = time.time()


def _clarification_dir_snapshot(folder: Path) -> dict[str, tuple[float, int]]:
    """{filename: (mtime, size)} for every .md in `folder`, taken immediately BEFORE a
    clarify session starts so `_clarification_report_files` can tell afterwards which
    files that session actually touched. Size rides along with mtime purely as a
    backstop against a filesystem whose timestamp granularity is coarse enough to hide
    a modification made in the same tick as the snapshot."""
    if not folder.exists():
        return {}
    snap: dict[str, tuple[float, int]] = {}
    for p in folder.glob("*.md"):
        try:
            st = p.stat()
        except OSError:
            continue
        snap[p.name] = (st.st_mtime, st.st_size)
    return snap


def _clarification_report_files(folder: Path, before: dict[str, tuple[float, int]]) -> list[Path]:
    """Return the .md files the session that just ran actually created or changed, judged
    against `before` (a `_clarification_dir_snapshot` taken just before it started).

    Compared against a snapshot rather than a wall-clock cutoff because mtime alone
    cannot distinguish "the agent wrote this" from "the dashboard wrote this a moment
    earlier": the answer UI's "Save & Clarify" button rewrites the file being answered
    (apply_answers_to_file in dashboard_api_clarify.py) and then starts a clarify run in
    the same breath, so a cutoff — especially one deliberately backdated to catch
    freshly-written files — swept that already-answered file up as a result of the new
    run. That mislabelled the run's output in the console and the attention
    notification, and, worse, `_stamp_clarify_timing` then wrote the new run's duration
    over the older file's own `clarify_seconds`, so every file but the newest ended up
    displaying the NEXT run's elapsed time in the dashboard's detail modal."""
    if not folder.exists():
        return []
    out: list[Path] = []
    for p in sorted(folder.glob("*.md")):
        try:
            st = p.stat()
        except OSError:
            continue
        if before.get(p.name) != (st.st_mtime, st.st_size):
            out.append(p)
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
    """Manual clarification — run ONE evaluation pass, report findings + report file(s), then
    suggest the next step based on what the round's severity phase still owes.

    The phase (config.json's "clarify_severity_phases", on by default) is what makes repeated
    manual rounds converge instead of drifting: a round evaluates at its phase's scope only —
    criticals alone while the critical sweep is unfinished — and the phase is persisted, so
    the next `tempa clarify` picks the sweep back up rather than restarting it at the widest
    scope. _advance_phase decides where it goes next; _print_manual_next_steps says so.

    Unless `noui` is set, also opens the clarification-answer web UI on the freshly written
    report file(s) so the user can answer right away instead of hand-editing the markdown.
    `skip_minor` keeps minor findings out of the run entirely (config.json's
    "skip_minor_findings" / CLI --skip-minor) — with phases on that means there is no minor
    phase, so the major phase is the last one."""
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

    phases_on = get_clarify_severity_phases(config)
    phase, clean_rounds = _load_phase_state(config, phases_on)
    severity_scope = _phase_scope(phase, phases_on, skip_minor)

    _banner(f"Clarify (manual) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}"
            + (f" | phase={_phase_label(phase)}" if phases_on else ""))

    start_ts = time.time()
    before_files = _clarification_dir_snapshot(clar_dir)
    prompt, carried_ledger = _build_evaluate_prompt(config, clar_dir, skip_minor, severity_scope)
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

    coverage = _round_coverage(clar_dir, carried_ledger)
    config = load_config()
    findings = config.get("last_clarification_findings", {})
    critical = findings.get("critical", 0)
    major = findings.get("major", 0)
    minor = findings.get("minor", 0)
    _stamp_clean_evaluation_if_zero(config, critical, major, minor, severity_scope)
    # Stamps *how* the current last_clarification_findings was produced — an
    # evaluate pass here, vs an apply pass in _run_apply_step() — so the dashboard's
    # finalize gate can tell "criticals were answered and applied" apart from "a
    # fresh evaluation independently confirmed 0 criticals remain".
    config["last_clarification_action"] = "evaluate"
    # Which severities this round actually looked for. Read by
    # tempa_config.severity_sweep_pending: a critical-only round reports major=0 without
    # measuring it, and the Start Implementation gate must not read that as "no majors".
    config["last_evaluation_scope"] = severity_scope
    config["last_clarification_round"] = config.get("last_clarification_round", 0) + 1
    blocking = _phase_blocking_count(phase, critical, major, minor)
    next_phase, clean_rounds, transition = _advance_phase(
        phase, phases_on, skip_minor, critical, blocking, coverage, clean_rounds)
    _save_phase_state(config, next_phase, clean_rounds)
    save_config(config)
    report_files = _clarification_report_files(clar_dir, before_files)
    _stamp_clarify_timing(report_files, "clarify_seconds", time.time() - start_ts)

    _banner(f"CLARIFICATION EVALUATION RESULT — critical={critical} major={major} minor={minor}")
    if report_files:
        for f in report_files:
            print(f"  {_hyperlink(f)}", flush=True)
    else:
        print(f"  (No new file detected — check the folder manually: {clarifications_path})", flush=True)

    _print_manual_next_steps(phase, next_phase, transition, phases_on,
                             critical, major, minor, blocking)

    if blocking:
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


def _print_manual_next_steps(phase: str, next_phase: str, transition: str, phases_on: bool,
                             critical: int, major: int, minor: int, blocking: int) -> None:
    """What a manual round tells the user to do next, given where _advance_phase just left the
    phase machine.

    Split out of run_clarify_once because the phases turned one three-way branch on severity
    into a four-way branch on transition, and because getting it wrong is expensive in a
    specific way: a critical-only round reports major=0 for a reason that has nothing to do
    with the spec, so the pre-phases "no critical/major — clarification is DONE" line would
    now be printed over a spec whose majors have never been looked at."""
    answer_steps = (
        "  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer\n"
        "  2. Clarify again to check what's left:                     tempa clarify\n"
        "     (answers are carried into the next round — no need to apply first)\n"
        "  3. Write the answers into the PRD/spec:                    tempa clarify --apply\n"
        "  (Or do all of it unattended, evaluate+answer loop:         tempa clarify --finalize)"
    )

    if transition == "demoted":
        print(f"[!] {critical} critical finding(s) turned up during the "
              f"{_phase_label(phase)} — answering a major rewrites the spec, and this is that "
              "rewrite's fallout.", flush=True)
        print("    The next round narrows back to criticals only until they're clear again.",
              flush=True)
        print(answer_steps, flush=True)
        return

    if blocking:
        if phase == _PHASE_CRITICAL and phases_on:
            print(f"[!] {critical} critical finding(s). This round looked for criticals ONLY — "
                  "majors and minors are not being evaluated yet, by design.", flush=True)
        elif critical:
            print(f"[!] There are {critical} critical finding(s). Next steps:", flush=True)
        else:
            print(f"Only {major} major finding(s) remain (no critical). Next steps:", flush=True)
        print(answer_steps, flush=True)
        return

    if transition == "stay":
        # Clean, but nothing backs that up yet — see _phase_may_advance.
        print(f"Nothing left in the {_phase_label(phase)} this round, but it isn't "
              "confirmed: this round left no complete coverage ledger behind.", flush=True)
        print("     Run tempa clarify once more to confirm before moving on.", flush=True)
        return

    if transition == "advanced":
        print(f"[OK] The {_phase_label(phase)} is clean and confirmed — nothing at that "
              "severity is left.", flush=True)
        print(f"     The next round moves on to the {_phase_label(next_phase)}: "
              "tempa clarify", flush=True)
        return

    print("[OK] No critical/major findings — clarification is considered DONE.", flush=True)
    if minor:
        print(f"     (Still {minor} minor finding(s) — considered acceptable.)", flush=True)
    print("     Write the answers into the PRD/spec:  tempa clarify --apply  (required before implementing)", flush=True)
    print("     Then move on to the next stage:       tempa implement  (auto plan runs first)", flush=True)


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

    before_files = _clarification_dir_snapshot(clar_dir)
    if not _run_auto_answer_step(config, unanswered_files):
        sys.exit(1)

    config = load_config()
    answered = config.get("last_auto_answer", 0)
    changed = _clarification_report_files(clar_dir, before_files)

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
    evaluate pass carries, and the loop writes the whole lot into the PRD at the closing
    compaction (plus any periodic checkpoints along the way — see _run_checkpoint). A
    pre-loop apply would just be an extra full agent session writing text that the rounds
    after it are about to revise anyway.

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


def _exit_if_graceful_stop(about_to: str) -> None:
    """Honour a pending graceful stop, if there is one, instead of spending another agent
    session on `about_to`.

    Called only at points where the state on disk is complete and resumable — the round's
    findings and last_finalize_round/_phase are already saved, and the pending overlay is
    untouched — so a stopped run picks up from exactly where it left off. The one thing a
    graceful stop must never do is interrupt a session that is already being paid for,
    which is why every call site sits BEFORE a session rather than inside one."""
    if not graceful_stop_requested("clarify"):
        return
    clear_graceful_stop("clarify")
    log(f"Clarify (finalize) stopped at your request, before {about_to} — the round in "
        "progress was allowed to finish first, so nothing already paid for was thrown "
        "away. Run Finalized Clarification again to continue from here.")
    sys.exit(0)


def _finalize_evaluate_round(config: dict, clar_dir: Path, run_number: int, round_kind: str,
                             skip_minor: bool, severity_scope: str = "critical_major",
                             ) -> tuple[int, int, int, dict | None]:
    """Run one evaluate pass of the finalize loop and return its (critical, major, minor)
    finding counts plus the coverage summary of the ledger it wrote, having already recorded
    the counts in config.json.

    `round_kind` is "evaluate" or "verify" (config.json's "last_finalize_phase") — which kind
    of round this is, NOT which severity phase it belongs to. `severity_scope` is the phase's
    (_PHASE_SCOPES); the two are independent, since every phase has both kinds of round.

    The prompt is rebuilt every round rather than reused: auto-answering grows the pending
    overlay, a compaction empties it (so the verify round evaluates the PRD on its own merits,
    which is the whole point of running it), and the coverage ledger it carries changes every
    round by construction.

    Exits the process rather than returning if the session couldn't run — 3 on an
    authentication error, 1 on any other failure, matching what a failed manual round does.
    """
    prompt, carried_ledger = _build_evaluate_prompt(config, clar_dir, skip_minor, severity_scope)

    start_ts = time.time()
    before_files = _clarification_dir_snapshot(clar_dir)
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

    coverage = _round_coverage(clar_dir, carried_ledger)
    config = load_config()
    findings = config.get("last_clarification_findings", {})
    critical = findings.get("critical", 0)
    major = findings.get("major", 0)
    minor = findings.get("minor", 0)
    _stamp_clean_evaluation_if_zero(config, critical, major, minor, severity_scope)
    config["last_clarification_action"] = "evaluate"
    config["last_evaluation_scope"] = severity_scope
    # Running total across every evaluate pass ever (manual `clarify` or one iteration
    # of `clarify --finalize`) — NOT reset here, unlike last_finalize_round below, so
    # it keeps counting across finalize runs and manual runs alike.
    config["last_clarification_round"] = config.get("last_clarification_round", 0) + 1
    config["last_finalize_round"] = run_number
    config["last_finalize_phase"] = round_kind
    save_config(config)
    report_files = _clarification_report_files(clar_dir, before_files)
    _stamp_clarify_timing(report_files, "clarify_seconds", time.time() - start_ts)

    log(f"Round #{run_number} ({round_kind}) findings: critical={critical}, major={major}, minor={minor}")
    return critical, major, minor, coverage


def _compact_resolutions_into_documents(clar_dir: Path, run_number: int, compactions: int,
                                        phase_label: str = "critical/major") -> int | None:
    """Nothing this phase is responsible for is left to ask, so write the whole accumulated
    overlay into the PRD/spec in one apply pass. Returns the incremented compaction count.

    Returns None instead when there was nothing left to write. The caller decides what that
    means, because it depends on where the phase machine is: at the end of the last phase it
    means the run is finished, but at a phase boundary it only means this phase decided
    nothing that isn't already in the documents, and the next phase still has to run.

    Exits 1 if the apply failed or the PRD has already been rewritten MAX_COMPACTIONS times
    without the verification round coming back clean — at that point the remaining findings
    need a human. That budget is per phase (the caller resets it on a phase change): it bounds
    the "verification came back dirty, rewrite again" loop, which is a thing that happens
    within one phase's sweep, not across the run.
    """
    config = load_config()
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, unapplied_files = _clarification_backlog(clar_dir, applied_hashes)
    if not unanswered_files and not unapplied_files:
        return None

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

    log(f"Nothing left in the {phase_label} — writing {len(unanswered_files) + len(unapplied_files)} "
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
    _exit_if_graceful_stop("writing the resolutions into the PRD/spec")
    if not _run_apply_step(config, resume_session_id=resume_sid):
        log(f"Apply-clarification run #{run_number} failed — stopping the loop.")
        notify_attention(
            AttentionEventType.CLARIFICATION_FAILED, "Clarification",
            f"Clarification apply round {run_number} failed",
            "Review the apply session log and resolve the failure before continuing.",
            details={"round": run_number},
        )
        sys.exit(1)
    return compactions


def _track_finalize_convergence(blocking: int, critical: int, major: int, prev_total: int | None,
                                no_progress_rounds: int, no_progress_limit: int | float,
                                max_run: int) -> tuple[int, int]:
    """Convergence guard: if `no_progress_limit` rounds in a row fail to reduce `blocking` —
    the count of findings in the severities the CURRENT phase is responsible for
    (_phase_blocking_count) — evaluate+auto-answer has run out of resolutions it can make on
    its own (e.g. every remaining finding genuinely needs a human decision) — exit 1 instead
    of burning up to max_clarification_run rounds of full-PRD re-evaluation for no benefit.

    Judged on the phase's own count rather than critical+major because those are different
    numbers once phases exist: in the critical phase a round that traded one critical for
    three majors has made progress on what that phase is for, and a round that changed
    nothing about criticals has not, whatever happened to the majors it never looked at.

    `prev_total is None` means there is no comparable baseline yet, so this round is not
    judged. That skips ONE comparison and deliberately does not clear the counter — a caller
    that means "forget everything so far" says so by resetting no_progress_rounds itself, the
    way the compaction does. Keeping the two separate is what stops a future caller from
    silently disabling the guard by reaching for the baseline reset alone.

    Returns the updated (prev_total, no_progress_rounds) when the loop may continue."""
    total = blocking
    if prev_total is not None:
        no_progress_rounds = no_progress_rounds + 1 if total >= prev_total else 0
    if no_progress_rounds >= no_progress_limit:
        log(f"No reduction in this phase's findings for {no_progress_rounds} round(s) in a row "
            f"— stopping instead of continuing to {max_run} rounds. {critical} critical and {major} "
            "major finding(s) remain and likely need a human decision (see `tempa answer`).")
        notify_attention(
            AttentionEventType.CLARIFICATION_ANSWERS_REQUIRED, "Clarification",
            "Clarification needs human answers",
            "Review and answer the remaining findings before continuing finalization.",
            details={"critical": critical, "major": major},
        )
        sys.exit(1)
    return total, no_progress_rounds


def _auto_answer_finalize_round(clar_dir: Path, run_number: int, critical: int, major: int) -> None:
    """Answer what this round found — never apply here. The answers join the pending overlay
    and are carried into the next evaluation; the PRD isn't touched until the next checkpoint
    (_run_checkpoint) or the closing compaction, whichever comes first.
    Exits 1 if the auto-answer session failed."""
    config = load_config()
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, _ = _clarification_backlog(clar_dir, applied_hashes)
    if not unanswered_files:
        log(f"The evaluation reported {critical} critical and {major} major finding(s) but left "
            "no unanswered finding behind — nothing to auto-answer this round.")
        return
    _exit_if_graceful_stop("auto-answering this round's findings")
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


def _commit_checkpoint(config: dict, commit_message: str) -> None:
    """Commit the workspace — the durability half a checkpoint and a successful finalize run
    share.

    `ensure_prd_tracked` runs first because the PRD lives under `.tempa/`, which Tempa's own
    `init` git-ignores: without the ignore rules it writes, `git add -A` would stage
    everything in the working folder EXCEPT the documents this commit exists to capture.
    Normally `init` has already done this, so the call is a no-op read; it is repeated here so
    a workspace scaffolded before those rules existed, and left open ever since, still gets
    its PRD committed rather than silently committing nothing.

    Neither step can fail the run. Both helpers return (outcome, detail) and never raise, and
    this only logs what they say — the same treatment the QA-pass commit gets in
    tempa_session_runners. Killing an unattended finalize run that has spent hours of agent
    time because git has no user.email configured would destroy far more than the commit was
    protecting."""
    if not get_finalize_checkpoint_commit(config):
        return
    workspace_root = get_workspace(config).get("root", "")
    outcome, detail = ensure_prd_tracked(workspace_root)
    # "unchanged"/"skipped" is the normal case every round after the first — not worth a line.
    if outcome not in ("unchanged", "skipped"):
        log(f".gitignore {outcome}: {detail}")
    outcome, detail = commit_workspace_changes(workspace_root, commit_message)
    log(f"Checkpoint commit {outcome}: {detail}")


def _finalize_success_commit() -> None:
    """Commit the PRD as the final, ready-to-implement version, right before a
    `clarify --finalize` run exits successfully.

    Runs on BOTH success exits (a clean verification round, and the "everything is already
    applied" exit inside _compact_resolutions_into_documents), and runs regardless of
    finalize_checkpoint_rounds — a run short enough never to checkpoint, or one with
    checkpoints switched off entirely, still ends with a PRD worth committing. Only the
    commit toggle gates it."""
    _commit_checkpoint(
        load_config(),
        "tempa: clarification finalized — PRD ready for implementation")


def _run_checkpoint(clar_dir: Path, run_number: int) -> None:
    """Periodic mid-loop save point (config.json's finalize_checkpoint_rounds): write the
    answers accumulated so far into the PRD/spec, then commit.

    Deliberately NOT routed through _compact_resolutions_into_documents. That function exits 0
    on an empty backlog — correct at the end of a run, fatal in the middle of one — and spends
    the MAX_COMPACTIONS budget, which bounds only the "verification came back dirty" rewrite
    loop. A checkpoint is a save point, not a rewrite attempt, so it goes straight to
    _run_apply_step and never touches that budget."""
    config = load_config()
    applied_hashes = config.get("clarify_applied_hashes", {}) or {}
    unanswered_files, unapplied_files = _clarification_backlog(clar_dir, applied_hashes)
    if not unanswered_files and not unapplied_files:
        log(f"Checkpoint at round {run_number}: every recorded answer is already in the "
            "PRD/spec — nothing to save.")
        return

    header = (f"CHECKPOINT — round {run_number} — apply, back up, commit — "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _banner(header)
    log(header, to_console=False)
    log(f"Writing {len(unanswered_files) + len(unapplied_files)} pending file(s) of "
        "resolutions into the PRD/spec documents...")

    # Resume the most recent clarify session, which here is the AUTO-ANSWER session that just
    # ran (run_clarification_session records its id for every clarify-stage session, and
    # auto-answer runs on the clarify_apply backend). Unlike the compaction's case — where the
    # resumed session is the evaluate pass — that session is the one that just worked out the
    # very answers this apply has to write, so its context is exactly what's needed. Checked
    # against the clarify_apply backend because that's the CLI that will attempt the --resume.
    resume_sid = get_clarify_session_id(config, get_backend(config, "clarify_apply"))
    _exit_if_graceful_stop("writing this checkpoint's answers into the PRD/spec")
    if not _run_apply_step(config, resume_session_id=resume_sid):
        log(f"Checkpoint apply at round {run_number} failed — stopping the loop.")
        notify_attention(
            AttentionEventType.CLARIFICATION_FAILED, "Clarification",
            f"Clarification checkpoint apply round {run_number} failed",
            "Review the apply session log and resolve the failure before continuing.",
            details={"round": run_number},
        )
        sys.exit(1)

    # Re-read: the apply rewrote clarify_applied_hashes and last_clarification_action.
    _commit_checkpoint(load_config(),
                       f"tempa: clarification checkpoint — round {run_number}")


def _advance_to_next_phase(clar_dir: Path, run_number: int, compactions: int,
                           settled: str, following: str) -> None:
    """Close out a settled severity phase: write everything it decided into the PRD/spec and
    commit, so the phase boundary is a readable diff and a restorable point, then say what the
    next phase will look for.

    There is deliberately no verification round here, unlike at the end of the run. The next
    phase's scope is this one's plus one severity (_PHASE_SCOPES widens rather than switches),
    so the very next round re-checks this phase's severities over the freshly compacted
    documents anyway — and a critical it turns up demotes the machine straight back. A
    separate verification round would buy exactly what the next round already does."""
    compacted = _compact_resolutions_into_documents(
        clar_dir, run_number, compactions, _phase_label(settled))
    if compacted is None:
        log(f"The {_phase_label(settled)} is clean and confirmed, and every answer it recorded "
            "is already in the PRD/spec.")
    else:
        _commit_checkpoint(load_config(),
                           f"tempa: clarification — {_phase_label(settled)} clean")
    log(f"Moving on to the {_phase_label(following)}.")


def _exit_if_critical_phase_exhausted(rounds: int, limit: int | float, critical: int) -> None:
    """Stop the run once the critical phase has spent `limit` answering rounds (config.json's
    "critical_phase_max_rounds") without clearing.

    Separate from the convergence guard because it catches the opposite case: a critical phase
    that IS making progress, one or two criticals at a time, round after round. That is a
    specification the rubric calls unbuildable in more ways than unattended answering should be
    settling on its own, and every one of those rounds is a full re-read of the whole PRD."""
    if rounds <= limit:
        return
    log(f"The critical sweep has spent {rounds} answering round(s) without clearing — stopping "
        f"instead of answering criticals unattended for longer. {critical} critical finding(s) "
        "remain, and a critical is the specification being unbuildable, which is worth a human "
        "decision (see `tempa answer`).")
    notify_attention(
        AttentionEventType.CLARIFICATION_ANSWERS_REQUIRED, "Clarification",
        "Clarification needs human answers",
        "The critical sweep has run its round budget without clearing — review the remaining "
        "critical findings by hand.",
        details={"critical": critical, "critical_phase_rounds": rounds},
    )
    sys.exit(1)


def run_clarify_finalize(skip_minor: bool = False) -> None:
    """Unattended clarification: loop evaluate -> auto-answer until an evaluation reports
    nothing left in the severity phase it is on, advancing a phase at a time, then write every
    accumulated answer into the PRD/spec in ONE apply pass ("compaction"), then run one more
    evaluation to verify the updated documents.

    The phases (config.json's "clarify_severity_phases", on by default) are what stop the loop
    sweeping a moving target. A round can only answer what it found, answering a major rewrites
    the spec, and a rewritten spec grows new criticals — so a loop that looks for both at once
    keeps re-deriving criticals from documents its own last round changed. Sweeping criticals
    alone until they are exhausted, then widening to majors, means the expensive severity is
    settled against a spec that is holding still. A critical that turns up later demotes the
    machine straight back to the critical phase (_advance_phase), which is the case the widening
    scope exists to catch. With the setting off, there is one phase, its scope is the pre-phases
    critical+major, and the loop below behaves exactly as it did before phases existed.

    No apply runs *per round*. Each evaluate carries every answer recorded so far as the
    pending overlay (_pending_overlay), so the agent judges the spec as it will read once
    those decisions are applied — which is what makes a per-round PRD rewrite unnecessary.
    One apply at the end replaces N of them.

    What that costs is durability: a long run holds hours of agent work in an overlay the PRD
    has never seen, with no restorable point until the very last step. `finalize_checkpoint_rounds`
    (default 5) buys some of it back — every N answering rounds the loop stops to apply and commit
    (_run_checkpoint), leaving a readable per-checkpoint diff of how the PRD actually changed.
    That is a trade for recoverability, not for evaluation quality: the overlay already made
    those applies unnecessary for correctness. Set it to null to get the pure
    one-apply-at-the-end behavior. A successful run always ends with one final commit
    (_finalize_success_commit), whatever the cadence.

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
    # Snapshotted at run start alongside max_run and no_progress_limit, and for the same
    # reason: it is loop cadence, and "checkpoint every 5" means nothing definite if the loop
    # re-reads it every round. Saving a new value mid-run warns about exactly that — see
    # _FINALIZE_SNAPSHOT_LIMITS in dashboard_runs.py. The commit toggle is a per-action
    # switch instead, and _commit_checkpoint reads it fresh every time.
    checkpoint_rounds = get_finalize_checkpoint_rounds(config)
    phases_on = get_clarify_severity_phases(config)
    # Resumed from config rather than restarted at the critical phase: a finalize run picks up
    # a sweep that manual rounds (or a stopped earlier run) already got part-way through, the
    # same way it picks up their pending overlay.
    severity_phase, clean_rounds = _load_phase_state(config, phases_on)
    critical_phase_limit = get_critical_phase_max_rounds(config)
    critical_phase_rounds = 0
    run_number = 0
    no_progress_rounds = 0
    prev_total = None  # the phase's own finding count last round, for the convergence guard
    # Per PHASE, not per run — the caller resets it at every phase boundary. See
    # _compact_resolutions_into_documents.
    compactions = 0
    # Answering rounds whose results are still only in the overlay. Counted rather than
    # derived from run_number % checkpoint_rounds: what a checkpoint is for is "N rounds of
    # answers have piled up since anything was last written", and modulo would instead fire
    # a full apply session right after a compaction had already emptied the overlay.
    rounds_since_checkpoint = 0
    # "evaluate" while findings are still being found and answered; "verify" for the round
    # that checks the PRD right after a compaction (same prompt, but with an empty overlay
    # in front of it, since applying just emptied it). Independent of the severity phase —
    # every phase has both kinds of round.
    round_kind = "evaluate"

    _banner(f"Clarify (finalize) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path} | "
            f"max_runs={max_run} | checkpoints={checkpoint_rounds or 'off'} | "
            f"phase={_phase_label(severity_phase) if phases_on else 'off'}")

    # Same reasoning as tempa_implement.main(): a stop is a request aimed at THIS run, so
    # a sentinel left behind by a previous one must not stop this one before it starts.
    clear_graceful_stop("clarify")

    _prepare_finalize_backlog(config, clar_dir)

    # Reset the finalize-only round counter to 0 for every fresh `clarify --finalize`
    # invocation — unlike last_clarification_round below (a running total across every
    # evaluate pass ever, manual or finalize), this one exists purely to show progress
    # against max_run for THIS run, so it always restarts from 0 rather than picking up
    # wherever a previous finalize run (or manual clarify) left off.
    config["last_finalize_round"] = 0
    save_config(config)

    while run_number < max_run:
        _exit_if_graceful_stop("starting another round")
        run_number += 1
        severity_scope = _phase_scope(severity_phase, phases_on, skip_minor)

        round_header = (f"ROUND {run_number}/{max_run} — {_phase_label(severity_phase)} — "
                        f"{round_kind} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        _banner(round_header)
        log(round_header, to_console=False)

        critical, major, minor, coverage = _finalize_evaluate_round(
            load_config(), clar_dir, run_number, round_kind, skip_minor, severity_scope)

        blocking = _phase_blocking_count(severity_phase, critical, major, minor)
        settled_phase = severity_phase
        severity_phase, clean_rounds, transition = _advance_phase(
            settled_phase, phases_on, skip_minor, critical, blocking, coverage, clean_rounds)
        phase_config = load_config()
        _save_phase_state(phase_config, severity_phase, clean_rounds)
        save_config(phase_config)

        if transition == "done":
            if round_kind == "verify":
                log("Verification round is clean — the PRD/spec documents now contain every "
                    "recorded resolution. Clarify (finalize) done.")
                if minor > 0:
                    log(f"Still {minor} minor finding(s) — considered acceptable.")
                _finalize_success_commit()
                sys.exit(0)

            compacted = _compact_resolutions_into_documents(
                clar_dir, run_number, compactions, _phase_label(settled_phase))
            if compacted is None:
                log("Nothing left to ask about, and every recorded answer is already in the "
                    "PRD/spec. Clarify (finalize) done.")
                if minor > 0:
                    log(f"Still {minor} minor finding(s) — considered acceptable.")
                _finalize_success_commit()
                sys.exit(0)
            compactions = compacted

            # The apply just rewrote the PRD, so a finding count from before it says nothing
            # about whether the loop is still making progress — comparing the verify round
            # against it would trip the convergence guard on the very first verification.
            prev_total = None
            no_progress_rounds = 0
            # The compaction just wrote the whole overlay — nothing has piled up since.
            rounds_since_checkpoint = 0
            round_kind = "verify"
            log("Resolutions written into the PRD/spec. Running a verification evaluation "
                "against the updated documents...")
            continue

        if transition == "advanced":
            _advance_to_next_phase(clar_dir, run_number, compactions,
                                   settled_phase, severity_phase)
            # Every one of these is scoped to the phase that just ended: its compaction budget,
            # its progress baseline, and the answers it piled into the overlay (which
            # _advance_to_next_phase has just written out).
            compactions = 0
            prev_total = None
            no_progress_rounds = 0
            rounds_since_checkpoint = 0
            round_kind = "evaluate"
            continue

        if transition == "demoted":
            log(f"{critical} critical finding(s) turned up during the "
                f"{_phase_label(settled_phase)} — narrowing back to criticals only until they "
                "are clear again. Answering a major rewrites the spec, and this is that "
                "rewrite's fallout.")
            prev_total = None
            no_progress_rounds = 0
            compactions = 0
        elif blocking == 0:
            # Clean, but nothing checkable backs that up yet (_phase_may_advance). One more
            # round at the same scope confirms it, and there is nothing to answer in between.
            log(f"Nothing left in the {_phase_label(settled_phase)} this round, but no "
                "complete coverage ledger backs that up — running one more round at the same "
                "scope to confirm before moving on.")
            round_kind = "evaluate"
            continue

        if phases_on and severity_phase == _PHASE_CRITICAL:
            critical_phase_rounds += 1
            _exit_if_critical_phase_exhausted(
                critical_phase_rounds, critical_phase_limit, critical)

        prev_total, no_progress_rounds = _track_finalize_convergence(
            blocking, critical, major, prev_total, no_progress_rounds, no_progress_limit, max_run)
        _auto_answer_finalize_round(clar_dir, run_number, critical, major)
        round_kind = "evaluate"
        rounds_since_checkpoint += 1
        if checkpoint_rounds and rounds_since_checkpoint >= checkpoint_rounds:
            _run_checkpoint(clar_dir, run_number)
            rounds_since_checkpoint = 0
            # prev_total is deliberately LEFT ALONE, unlike after a compaction. The overlay
            # means an evaluate round already counts findings as the PRD will read once the
            # recorded answers are applied, so applying them changes nothing about what the
            # next round measures — the counts stay comparable and the convergence guard keeps
            # working. (The compaction resets it for a different reason: the round before one
            # is clean by definition, so 0 -> anything would read as a round that lost ground.)
            # Resetting here would also make the guard unreachable at a low interval, since
            # every comparison would be the one being skipped.
            # round_kind stays "evaluate". A clean next round then lands in
            # _compact_resolutions_into_documents, which finds an empty backlog and exits 0
            # with an accurate message BEFORE spending a compaction — so nothing is wasted by
            # not calling it "verify".

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
