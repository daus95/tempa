"""Per-epic QA round history and the loop guard that reads it.

Every QA verdict an epic receives is appended to `epic["qa_history"]` by `record_qa_round`
(called from `tempa_session.run_qa_session` once a session actually lands a verdict), and
`detect_qa_loop` reads that history back to recognize an epic that is cycling through the QA
gate instead of converging on a pass.

The loop this exists to catch: QA fails on features 1-2, the fix session repairs them and marks
the epic done, QA fails on features 3-4, fixing those regresses 1-2, and round after round the
epic never converges. None of the older anti-loop guards see it — `implement_no_progress_rounds`
is only evaluated while an epic is still on_progress/require_fixing after a session (a successful
fix round sets it "done", skipping that branch entirely), and `total_run` is reset to 0 on any
forward progress, which such a round always makes. Meanwhile `require_fixing` outranks `pending`
in the scheduler, so an oscillating epic blocks every later epic for as long as it runs.

Everything here is a pure function over the epic dict — no I/O, no locks, no config reads — so
the rules can be exercised directly in tests. Callers mutate and save.
"""

from __future__ import annotations

from datetime import datetime

# How many rounds are kept on the epic. The history rides along in config.json, which the
# spawned agent re-reads every session, so it is deliberately short: enough to see a cycle
# (a repeat needs 3-4 rounds) plus a little context, not an audit trail. Old rounds age out.
_MAX_HISTORY = 8

# How many rounds `format_qa_history` renders. Its output goes into `blocked_reason`, which
# `tempa status` and the dashboard print inline — a wall of rounds there helps nobody.
_MAX_RENDERED = 6

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
# Written by the reset commands rather than by a QA session: it marks where a human
# intervened, and `detect_qa_loop` ignores everything before the newest one. See
# tempa_maintenance.reset_failed_epics / reset_qa_state.
VERDICT_RESET = "reset"


def _rounds_since_reset(epic: dict) -> list[dict]:
    """Every history entry after the most recent `reset` sentinel (all of them if there is
    none). This is what makes `tempa implement --reset-failed` a genuine clean slate for the
    guard while still keeping the pre-reset rounds on the epic as evidence for a human."""
    history = epic.get("qa_history") or []
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("verdict") == VERDICT_RESET:
            return history[i + 1:]
    return list(history)


def _fail_rounds(entries: list[dict]) -> list[dict]:
    return [entry for entry in entries if entry.get("verdict") == VERDICT_FAIL]


def record_qa_round(
    epic: dict, verdict: str, failed_ids: list[str] | None = None, report: str = "",
) -> dict:
    """Append this QA round's outcome to `epic["qa_history"]` and return the entry.

    `failed_ids` are the features the QA agent flagged (ids of the ones it left marked
    "require_fixing"), which is what `detect_qa_loop` fingerprints a round by. It can legitimately
    be empty on a fail verdict — the per-feature bookkeeping is written by the agent and nothing
    enforces it — and the detection rules account for that; see `detect_qa_loop`.

    Mutates `epic` in place, mirroring how `total_run`/`no_progress_rounds` are already tracked
    directly on it. The caller still has to save_config."""
    history = epic.setdefault("qa_history", [])
    # Numbered from the previous entry, not from len(history) — the list is trimmed to the
    # newest _MAX_HISTORY entries, so counting its length would restart the numbering (and
    # repeat it) as soon as an epic runs past the cap.
    previous = history[-1].get("round", 0) if history else 0
    entry = {
        "round": previous + 1,
        "verdict": verdict,
        "failed": sorted(failed_ids or []),
        "report": report,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    # One report file is one QA round. If the newest entry already points at the same report,
    # this is that same round being recorded a second time rather than a new one — overwrite it
    # in place (this call is the authoritative one) instead of appending a duplicate. Two rounds
    # with an identical failing set is exactly the shape `_cycle_round` and `_regressed_features`
    # read as an epic going in circles, so a double-record doesn't just pad the history: it
    # manufactures the very signal this module exists to detect. Seen in the wild when a QA agent
    # wrote a `qa_history` entry into config.json itself (nothing in any prompt asks it to) and
    # the runner then recorded the same round properly; also covers any future path that records
    # one round twice. Rounds with no report path can't be told apart this way, so they always
    # append.
    if report and history and history[-1].get("report") == report:
        entry["round"] = history[-1].get("round", entry["round"])
        history[-1] = entry
        return entry
    history.append(entry)
    del history[:-_MAX_HISTORY]
    return entry


def append_reset_marker(epic: dict) -> None:
    """Mark a human reset in the epic's QA history, so `detect_qa_loop` starts counting again
    from here. Called by the reset commands INSTEAD of deleting the history: deleting it would
    throw away the only record that this epic ever oscillated, while keeping it without a
    boundary would re-trip the guard on the very first round after the reset — the same dead end
    `reset_failed_epics` already documents for the other run/stall counters. No-op when there is
    nothing recorded yet, or when the newest entry is already a reset marker (repeated resets
    shouldn't each burn a history slot)."""
    history = epic.get("qa_history") or []
    if not history or history[-1].get("verdict") == VERDICT_RESET:
        return
    record_qa_round(epic, VERDICT_RESET)


def last_report_path(epic: dict, exclude: str = "") -> str:
    """The report file of the most recent QA round that produced one, or "" if there is none.

    This is what makes a QA round cumulative rather than a fresh opinion every time: the next
    round's prompt points the agent at it and asks it to re-verify those findings before looking
    for anything new (see tempa_prompts._build_previous_qa_findings). Without it each round
    re-derives its own view of the epic from scratch, flags a different subset, and the absence
    the loop guard reads as "QA checked this and was satisfied" means nothing.

    Reset markers and rounds that never wrote a report are skipped. `exclude` drops a path the
    caller knows is the round being started right now — a resumed QA session reuses the previous
    file name (see tempa_implement._resume_interrupted_qa), and pointing a round at its own
    half-written report would have it grade its own work in progress."""
    for entry in reversed(epic.get("qa_history") or []):
        if entry.get("verdict") == VERDICT_RESET:
            continue
        report = entry.get("report") or ""
        if report and report != exclude:
            return report
    return ""


def _regressed_features(fails: list[dict]) -> list[str]:
    """Features in the newest fail round that had already been fixed and re-verified once: they
    appear in an earlier round's failures, are absent from at least one round in between (QA
    checked them and was satisfied), and are failing again now.

    That absence is the whole signal. A feature failing in consecutive rounds is just a fix that
    hasn't landed yet — normal, and no reason to stop. A feature that passed and then broke again
    means a later round's work undid an earlier round's, which is the shape of a loop that does
    not terminate on its own.

    A round that flagged nothing at all is not evidence of anything and is skipped when looking
    for that absence. Its empty set makes EVERY feature look "absent, therefore re-verified", so
    counting it would read one round of missing bookkeeping as a wholesale regression — the same
    empty-fingerprint trap `_cycle_round` already refuses to fall into. Such rounds are still
    counted by `detect_qa_loop`'s `max_fail_rounds` backstop, which is what bounds that case."""
    if len(fails) < 3:
        return []
    current = set(fails[-1].get("failed") or [])
    if not current:
        return []
    regressed = set()
    for i, entry in enumerate(fails[:-1]):
        earlier = set(entry.get("failed") or [])
        recovered = earlier & current
        if not recovered:
            continue
        # "Absent from at least one round in between" — checked against the rounds strictly
        # between this earlier one and the current one.
        for between in fails[i + 1:-1]:
            flagged = set(between.get("failed") or [])
            if not flagged:
                continue
            regressed |= recovered - flagged
    return sorted(regressed)


def _cycle_round(fails: list[dict]) -> int | None:
    """The round number of an earlier fail whose failing-feature set is identical to the newest
    one, with at least one different set in between — i.e. the epic has come back around to a
    state it was already in. Returns None when there's no such repeat.

    Empty sets are never compared: a fail verdict that named no features (the agent skipped the
    per-feature bookkeeping) would otherwise match every other such round and report a cycle that
    nothing supports. That holds for the in-between rounds too — an empty one says nothing about
    what the epic looked like at that point, so it can't be the "different set in between" that
    turns a repeat into a round trip. Rounds like that are bounded by `max_fail_rounds` instead."""
    if len(fails) < 3:
        return None
    current = set(fails[-1].get("failed") or [])
    if not current:
        return None
    for i, entry in enumerate(fails[:-1]):
        if set(entry.get("failed") or []) != current:
            continue
        for between in fails[i + 1:-1]:
            flagged = set(between.get("failed") or [])
            if flagged and flagged != current:
                return entry.get("round")
    return None


def detect_qa_loop(epic: dict, strikes_limit: int, max_fail_rounds: int) -> str | None:
    """Decide whether `epic` is stuck cycling through the QA gate, and return a human-readable
    explanation if so (None = carry on). Mutates `epic["qa_loop_strikes"]`; the caller saves.

    Two independent ways to trip:

    1. A regression or a repeated failure set (see `_regressed_features` / `_cycle_round`) is one
       "strike"; `strikes_limit` consecutive strikes trips the guard. A clean round clears the
       count. Requiring more than one is deliberate: the QA agent is an LLM, so a single repeat
       can just be a round that tested more thoroughly than the one before it rather than a real
       regression — one round of tolerance costs little and avoids stopping a run over noise.

    2. `max_fail_rounds` failed rounds since the last reset, whatever the pattern. Both rules in
       (1) fingerprint a round by the feature statuses the AI agent writes, and nothing enforces
       that it writes them (the same class of slip `reconcile_qa_passed_features` and
       `_epic_features_actually_done` already exist to repair). When it doesn't, every round's
       fingerprint is empty, both rules correctly refuse to guess, and without this backstop the
       loop would run unbounded again. The pattern rules are what trip early and explain why;
       this is what guarantees the loop ends at all."""
    entries = _rounds_since_reset(epic)
    if not entries or entries[-1].get("verdict") != VERDICT_FAIL:
        # Nothing to judge: either no QA has run since the last reset, or the newest verdict was
        # a pass — which is the epic converging, so any strikes it accumulated are stale.
        if entries and entries[-1].get("verdict") == VERDICT_PASS:
            epic["qa_loop_strikes"] = 0
        return None

    fails = _fail_rounds(entries)
    label = epic.get("epic_name", "This epic")

    regressed = _regressed_features(fails)
    cycle_round = _cycle_round(fails)
    signalled = bool(regressed) or cycle_round is not None
    strikes = epic.get("qa_loop_strikes", 0) + 1 if signalled else 0
    epic["qa_loop_strikes"] = strikes

    if strikes >= strikes_limit:
        if regressed:
            what = (
                f"feature(s) {', '.join(regressed)} failed QA, were fixed and re-verified, and "
                "are failing again"
            )
        else:
            what = (
                f"this round's QA failures are the same set round {cycle_round} already reported, "
                "after a different set in between"
            )
        return (
            f"{label} is cycling through QA rather than converging: {what}. That is {strikes} "
            f"round(s) in a row showing it, so fixing what QA reports is undoing earlier work "
            "instead of finishing the epic. Stopping here rather than re-running QA "
            f"indefinitely.\n\n{format_qa_history(epic)}"
        )

    if len(fails) >= max_fail_rounds:
        return (
            f"{label} has now failed QA {len(fails)} time(s) without ever passing. Whatever each "
            "round fixes, the next round finds more — this is not converging on its own. Stopping "
            f"here rather than re-running QA indefinitely.\n\n{format_qa_history(epic)}"
        )

    return None


def format_qa_history(epic: dict) -> str:
    """Render the epic's recent QA rounds as a compact round-by-round block, for the
    `blocked_reason` a human reads in `tempa status` and the dashboard. Shows which features each
    round flagged, which is what makes a regression or a cycle visible at a glance instead of
    only being asserted."""
    history = (epic.get("qa_history") or [])[-_MAX_RENDERED:]
    if not history:
        return "No QA rounds recorded."
    lines = ["QA rounds so far:"]
    for entry in history:
        verdict = entry.get("verdict", "?")
        if verdict == VERDICT_RESET:
            lines.append(f"  round {entry.get('round', '?')}: — reset by hand, counting restarts here")
            continue
        failed = entry.get("failed") or []
        detail = ", ".join(failed) if failed else (
            "passed" if verdict == VERDICT_PASS else "failed, but no feature was flagged"
        )
        mark = "✅" if verdict == VERDICT_PASS else "❌"
        lines.append(f"  round {entry.get('round', '?')}: {mark} {detail}")
    return "\n".join(lines)
