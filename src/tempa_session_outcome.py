"""What a finished implementation session MEANS for its epic.

_run_backend_session (tempa_session.py) answers "did the process exit 0?". That is not the
same question as "did this epic make progress?", and the gap between the two is where all
the judgement lives: a usage-limit or auth stop is not a failure, a session cut short by the
backend killing its own background work is not a stalled epic, an exit-0 session that
completed no feature for several rounds running usually means the epic is blocked on
something another epic owns — which may be fixable by reordering the plan, or may be nothing
more than QA-state bookkeeping that drifted out of sync.

That decision tree (and the six helpers it leans on) lives here so the session engine next
door stays about running processes. apply_session_outcome() is called by run_session under
_state.lock and is the only entry point; everything else is a helper it uses.
"""

from __future__ import annotations

from pathlib import Path

from tempa_backend import Backend
from tempa_config import (
    LAST_ROUND_NOTE_UNFINISHED_CHECK,
    get_config_path,
    load_config,
    save_config,
)
from tempa_decisions import EPIC_DEFERRED, answer_hint, blocked_features, describe, has_other_work
from tempa_logging import RECLAIMED_LINE_MARKER, _state, log
from tempa_maintenance import _epic_features_actually_done, with_retry_hint
from tempa_notifications import AttentionEventType, notify_attention


def _last_meaningful_log_lines(log_path: Path, max_lines: int = 6) -> str:
    """The session's own closing explanation of what it did and why it stopped, for surfacing to
    a human (the no-forward-progress guard below puts it in `blocked_reason`, which is the whole
    of what the dashboard's Halted panel shows).

    Prefers what the agent actually said, captured off the event stream while the session ran
    (see `_remember_agent_message`). This used to tail the log file instead, which cannot be made
    to work: a tool result is logged as one `[Result] ...` chunk whose own content may span
    lines, and those continuation lines carry no marker, so the tail of a log is not separable
    into "what the agent said" and "what its last command printed". One epic's stored reason
    opened with a psql table header and `(0 rows)`; another's with an Edit tool's success message.

    A round that ended on a promise to come back now has Tempa's own account written above these
    words rather than these words alone (see `_waiting_close_notice`).

    The log tail is kept as the fallback for the case the capture cannot cover — a session that
    produced no prose at all, or one whose outcome is being recorded by a process that didn't run
    it (a test, a resumed run) — where six lines of possibly-mixed output still beat nothing."""
    captured = (_state.last_agent_message or "").strip()
    if captured:
        return "\n".join(captured.splitlines()[-max_lines:])
    try:
        lines = [line for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    # Drop Tempa's own reclaim line and anything after it. `_log_reclaimed` appends that message
    # into the session log AFTER the agent's last word, so on any session that left processes
    # running it IS the tail — and this fallback would then hand back Tempa's report of killing
    # them as the session's own explanation of why it stopped, on precisely the epic where that
    # is the most confusing sentence available ("Turn off Terminate leftover processes in
    # Settings → Runs", presented to a human as their epic's blocker). The existing [Done] trim
    # below cannot catch it: that only fires when [Done] is the LAST line, and once the reclaim
    # paragraph lands after it, it is not. Matched from the end and only near the start of a
    # line, so a session that happened to `cat` a Tempa log mid-round cannot truncate its own
    # explanation at the quoted copy — the accepted residue is that a quoted copy appearing as
    # the very last output would still trim, which costs a fallback reason, not a real one.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("[") and 0 <= lines[i].find(RECLAIMED_LINE_MARKER) <= 80:
            lines = lines[:i]
            break
    if lines and lines[-1].startswith("[Done]"):
        lines = lines[:-1]
    return "\n".join(lines[-max_lines:])


# Closing phrases that only make sense if the speaker expects to be called again. Deliberately
# NOT a list of ways to say "waiting": "blocked waiting on the client's API key" is a perfectly
# good blocker report that has to reach the Halted panel word for word, and no keyword tells it
# apart from "waiting for the test run I started" by topic — this repo's own fixture for the
# failure path is literally "waiting on something external". What separates them is TENSE: a
# promise about what this session will do later, which under Tempa is a promise about a session
# that stopped existing the moment it was written. Both rounds of the EPIC-02 incident ended on
# one — "I'll report back once it completes." and "I'll report back once it finishes."
#
# English, and shaped by two observations on one backend. A Copilot or Codex session, or a plan
# written in another language, may end a turn to wait in words this misses; that degrades to
# exactly the behaviour Tempa had before this existed, which is the safe direction. A list to
# extend from live logs, the way `usage_limit_markers` and `background_terminated_markers` grew.
_PROMISED_TO_COME_BACK = (
    "report back", "i'll report", "i will report", "let you know once", "let you know when",
    "update you once", "update you when", "check back", "circle back", "i'll resume",
    "i will resume", "i'll continue once", "i will continue once", "i'll pick this up",
    "standing by", "will follow", "results to follow", "once it completes", "once it finishes",
)


def _ended_waiting_on_killed_work() -> bool:
    """True when this round both promised to come back with a result and had the thing it was
    waiting on destroyed by Tempa's own container teardown.

    Two signals, both required, because either alone gets it wrong in a direction that matters.
    The prose alone cannot carry it, for the reason above. The reclaim alone cannot either, and
    the margin is not close: `terminate_leftover_processes` defaults on and agent CLIs leave dev
    servers behind constantly, so across the five containment-era process logs in the incident
    workspace 62 of 76 exit-0 sessions — 82% — reclaimed something, three of the five runs being
    entirely healthy. Anything keyed on reclaim alone is not a mechanism, it is `+1` to
    `implement_no_progress_rounds` with a story attached.

    Reads `_state.last_agent_message` rather than the six-line summary
    `_last_meaningful_log_lines` returns, and reads the message's LAST paragraph. 163 of the 178
    session logs in that workspace close with a prose block longer than six lines — the incident's
    own closing message happened to be one line, which is the only reason a tail-based version of
    this would have looked like it worked. The longest is 47 lines, whose final six are a fragment
    of a markdown bullet list.

    Stuck-after-done is excluded explicitly rather than inferred. It is tempting to argue that
    path always reclaims 0, because `_terminate_if_stuck_after_done` already called
    `group.terminate_tree()` — but Windows' `terminate_tree` counts first and leaves `self._job`
    set, so the later `close()` re-counts a job whose members `TerminateJobObject` may not have
    reaped, from a different thread, with nothing synchronising the two. And that path can reach
    here with exit code 0, since `apply_session_outcome`'s stall branch does not exclude the flag.
    Checking it directly costs one clause and removes the cross-thread reasoning from the
    correctness argument entirely."""
    if not _state.reclaimed_process_count or _state.backend_stuck_after_done_hit:
        return False
    paragraphs = [p for p in (_state.last_agent_message or "").split("\n\n") if p.strip()]
    if not paragraphs:
        return False
    closing = paragraphs[-1].lower()
    return any(phrase in closing for phrase in _PROMISED_TO_COME_BACK)


# One. Not a configurable allowance and not two: this is the difference between "Tempa does not
# manufacture the verdict on the very round it killed" and "a stalling epic gets a longer leash".
_CUT_SHORT_GRACE_ROUNDS = 1


def _bank_cut_short_grace(epic: dict) -> bool:
    """Whether `epic` still has a spare round because Tempa itself cut one of its sessions short,
    banking this one if so. Called only on the round that would otherwise fail the epic.

    Every session runs inside a container Tempa tears down when the backend CLI exits, and what
    that teardown kills is often real. Seen live on EPIC-02: session #1 spent 182 turns doing QA
    fixes for FEAT-02-02/04/05 and live-verifying all three, started a 12-minute `dotnet test` in
    the background, and closed its turn saying it would report back once the suite finished —
    which, in a headless one-shot run, is how a session ends. The CLI exited 0, the container
    reclaimed 38 processes, and the result that round was waiting on never existed. Session #2
    started over with no memory of the verification, did the same thing, and reclaimed 35 more.
    Two exit-0 rounds with no feature completed is precisely what `implement_no_progress_rounds`
    reads as an epic blocked on something outside itself, so EPIC-02 was marked failed and the
    whole runner stopped — over work Tempa had killed, on an epic that was blocked on nothing.

    Deliberately a bounded grace rather than the outright exemption
    `background_tasks_terminated_hit` grants, and deliberately spent HERE rather than by raising
    the limit at the top of `_handle_stalled_round`. That limit gates the entire triage, so
    raising it would also postpone `_try_reorder_for_dependency` and `_requeue_for_qa_after_desync`
    — two repairs that need no human at all, one of them the automatic fix for the config write
    race `save_config`'s own docstring documents. Spending the grace in the failure arm alone
    means `no_progress_rounds` keeps counting honestly (so the eventual halt says four rounds when
    four rounds happened), both repairs keep firing on the round they do today, and the only thing
    bought is one more attempt before a human is told.

    The balance is dropped the moment a feature completes (`_update_no_progress_tracking`): this
    is grace for an epic that was interrupted, not for one that is stuck."""
    banked = epic.get("cut_short_rounds", 0)
    if banked >= _CUT_SHORT_GRACE_ROUNDS:
        return False
    epic["cut_short_rounds"] = banked + 1
    return True


def _update_no_progress_tracking(epic: dict, completed_before: int, limit: int) -> bool:
    """Increment/reset `epic["no_progress_rounds"]` based on whether completed_features grew
    past `completed_before` this round, and return whether it has now reached `limit` — i.e.
    `limit` resumed sessions in a row finished (exit code 0) without completing another
    feature, which almost always means the epic is blocked on something outside itself (e.g.
    a dependency owned by a not-yet-implemented epic) rather than genuinely still working.

    Also resets `epic["total_run"]` (the max_session_run anti-loop counter) back to 0 on real
    progress — without this, a long-running epic that legitimately needs many resumes across
    its natural lifecycle (many features_per_session batches, several QA fix-rounds) keeps
    accumulating toward that lifetime cap for reasons that have nothing to do with being stuck,
    and can eventually hit it despite never having actually stalled — at which point it's left
    permanently stuck in `on_progress` (see _validate_and_increment_run) with no recovery, since
    `no_progress_rounds` never got a chance to reach `limit` and trigger the fix/failure path
    in _handle_stalled_round below. Resetting both together on progress means `no_progress_rounds`
    (this epic's own, much lower threshold) always has the chance to catch a genuine stall
    first, making max_session_run a redundant, higher backstop instead of a competing trap.

    Worth being precise about what that makes max_session_run: a backstop against a run of
    consecutive zero-progress sessions, and nothing wider. It is not a lifetime session cap and
    was never reached in the incident workspace — EPIC-13 has 20 session logs on disk and a stored
    `total_run` of 2, EPIC-03 has 21 QA rounds against a limit of 30 it never approached. Any
    sentence elsewhere that offers max_session_run as the remaining safety net for an epic that
    keeps making a little progress is overstating it.

    Mutates `epic` in place, mirroring how `total_run` is already tracked directly on it."""
    if epic.get("completed_features", 0) > completed_before:
        epic["no_progress_rounds"] = 0
        epic["total_run"] = 0
        # Whatever the last stalled round concluded is about a state this round just moved past;
        # carrying it into the next prompt would argue against work that has already happened.
        epic.pop("last_round_note", None)
        epic.pop("last_round_note_kind", None)
        # And the spare round Tempa's own teardown bought this epic: that grace was banked
        # against a stall this round has just ended. A later stall gets a fresh one, not this
        # one's change.
        epic.pop("cut_short_rounds", None)
        return False
    epic["no_progress_rounds"] = epic.get("no_progress_rounds", 0) + 1
    return epic["no_progress_rounds"] >= limit


def _try_reorder_for_dependency(config: dict, stuck_index: int, blocked_by_epic: str) -> str | None:
    """A stuck epic (`config["epic"][stuck_index]`) reported it's blocked on functionality
    owned by `blocked_by_epic` (see the "blocked_by_epic" rule in build_session_prompt) — an
    out-of-order dependency the plan scheduled too late. Try to fix that automatically by
    moving `blocked_by_epic` to immediately before the stuck epic in config["epic"], so the
    scheduler works on it next instead of endlessly re-resuming the stuck one.

    Returns None on success (mutates config["epic"] in place). Otherwise returns a short,
    human-readable reason it refused to act — the stuck epic should be marked failed instead,
    with this reason included, since none of these are safe to force through automatically:
    the named epic doesn't exist, is already done (so it's probably not the real blocker
    anymore), is already positioned before the stuck epic (reordering already happened but
    the block persists regardless), or reordering it would undo an earlier reorder in the
    opposite direction (a likely circular dependency between the two epics)."""
    epics = config["epic"]
    stuck_name = epics[stuck_index].get("epic_name")
    if blocked_by_epic == stuck_name:
        return "an epic can't be blocked on itself"
    target_index = next((i for i, e in enumerate(epics) if e.get("epic_name") == blocked_by_epic), None)
    if target_index is None:
        return f"'{blocked_by_epic}' is not a known epic in this plan"
    if epics[target_index].get("status") == "done":
        return f"'{blocked_by_epic}' is already done, so it's likely not the real blocker"
    if target_index < stuck_index:
        return f"'{blocked_by_epic}' is already scheduled before this epic — reordering already happened but the block persists"
    # The counterpart has already recorded that it is blocked on US. That is the cycle stated
    # outright, in config.json, before any reorder has been attempted — so catch it here rather
    # than waiting for the reverse reorder to be requested a round or two later and inferring it
    # from the history. Seen live: EPIC-02 sat with "blocked_by_epic": "EPIC-04" the whole time
    # EPIC-04's last feature was the one that had to rewrite EPIC-02's code.
    if epics[target_index].get("blocked_by_epic") == stuck_name:
        return _circular_dependency_reason(stuck_name, blocked_by_epic, mutual_declaration=True)
    history = config.setdefault("epic_reorder_history", [])
    if [stuck_name, blocked_by_epic] in history:
        return _circular_dependency_reason(stuck_name, blocked_by_epic, mutual_declaration=False)
    history.append([blocked_by_epic, stuck_name])
    epics.insert(stuck_index, epics.pop(target_index))
    return None


def _circular_dependency_reason(stuck_name: str, blocked_by_epic: str, *, mutual_declaration: bool) -> str:
    """Why a cycle is refused, and what a human can actually do about it.

    No reordering scheme satisfies "A before B" and "B before A" at once, so this is the one
    triage branch whose answer is always a plan change. Saying only "this looks like a circular
    dependency" left the reader to work out what kind of change from two epic specs and a log —
    the options are short and knowable, so they belong in the message that stops the run."""
    how = (
        f"'{blocked_by_epic}' has itself recorded that it is blocked on '{stuck_name}'"
        if mutual_declaration else
        f"moving '{stuck_name}' before '{blocked_by_epic}' already happened previously"
    )
    return (
        f"{how} — a circular dependency: the two epics depend on each other, and no ordering "
        f"satisfies both. This is a "
        f"plan design problem, not a bug in either epic's code, so it needs a plan change rather "
        f"than another round. Pick one: (a) merge them into a single epic; (b) have the earlier "
        f"one ship a real, permanent implementation the later one only consumes, instead of a "
        f"temporary one the later epic has to replace; or (c) move the replacement work into the "
        f"earlier epic as its own feature. Then reorder the \"epic\" array so every dependency "
        f"comes before what needs it, and clear \"blocked_by_epic\" on both."
    )


def _epic_genuinely_complete(epic: dict) -> bool:
    """True iff `epic`'s own feature-level bookkeeping proves it is genuinely fully
    implemented: it has a non-empty total_features, completed_features equals it, and
    _epic_features_actually_done confirms every individual feature agrees (that check
    alone isn't enough on its own here — it vacuously returns True when an epic has no
    "features" list at all, which is the right default for the QA gate it normally
    guards, but wrong for this use: an epic with total_features==0 has proven nothing).

    Used by _handle_stalled_round to tell a real external blocker apart
    from a QA-state bookkeeping desync (see _repair_qa_state_desync): an epic that made no
    forward progress across implement_no_progress_rounds resumed sessions AND satisfies
    this isn't actually stuck implementing anything — its code is done, only its epic-level
    status/QA fields disagree with that fact."""
    total = epic.get("total_features", 0)
    completed = epic.get("completed_features", 0)
    return total > 0 and completed == total and _epic_features_actually_done(epic)


def _repair_qa_state_desync(epic: dict) -> None:
    """Repair a QA-state bookkeeping desync in place: route `epic` back through the normal
    QA gate instead of the caller marking it failed. Sets status="done" (satisfies
    tempa_implement._run_qa_gate's "done and not qa_passed" condition),
    qa_passed=False and qa_status="idle" (matches what a fresh, never-QA'd epic looks
    like), and resets no_progress_rounds back to 0 so a genuinely new stall on this same
    epic after the repair gets its own full grace period instead of instantly re-tripping
    the limit again. Caller (_requeue_for_qa_after_desync) still has to save_config."""
    epic["status"] = "done"
    epic["qa_passed"] = False
    epic["qa_status"] = "idle"
    epic["no_progress_rounds"] = 0


def _reason_with_counterpart_context(reason: str, epics: list[dict], blocked_by_epic: str | None) -> str:
    """Append the counterpart epic's own last `blocked_reason` (if it has one) to `reason` —
    so a human deciding what to do about a stuck epic that couldn't be auto-reordered (most
    notably the circular-reversal refusal, where each epic is blocked on the other) sees both
    epics' own explanations in one place instead of having to go dig up the other one
    separately."""
    counterpart = next((e for e in epics if e.get("epic_name") == blocked_by_epic), None) if blocked_by_epic else None
    if counterpart and counterpart.get("blocked_reason"):
        return f"{reason}\n\nFor context, '{blocked_by_epic}' itself previously reported being blocked:\n{counterpart['blocked_reason']}"
    return reason


def _requeue_reordered_epic(config: dict, epic: dict, session_label: str, blocked_by_epic: str,
                            reason: str, rounds: int, log_path: Path) -> None:
    """The blocker the session named was moved ahead of this epic in the plan — put this one
    back in the queue behind it. Not a failure: nothing needs a human.

    `rounds` is this epic's own `no_progress_rounds`, not the configured threshold — the two
    agree today and the message is trying to state the first."""
    epic["no_progress_rounds"] = 0
    epic.pop("cut_short_rounds", None)
    epic["status"] = "pending"
    save_config(config)
    log(
        f"Session [{session_label}] made no progress for {rounds} resumed session(s) "
        f"in a row — it reported being blocked on '{blocked_by_epic}', which hasn't "
        "been implemented yet. Automatically moved it ahead in the plan so it runs "
        f"next; [{session_label}] will resume once it's done. Its own last "
        f"explanation:\n{reason}"
    )
    notify_attention(
        AttentionEventType.IMPLEMENTATION_AUTO_REORDERED,
        "Implementation",
        f"{session_label} was blocked on '{blocked_by_epic}' — reordered automatically",
        f"No action needed unless '{blocked_by_epic}' also gets stuck — "
        f"{session_label} will resume automatically once it's done.",
        epic=session_label,
        log_path=log_path,
        details={"reason": reason, "blocked_by_epic": blocked_by_epic},
    )


def _requeue_for_qa_after_desync(config: dict, epic: dict, session_label: str, reason: str,
                                 rounds: int, log_path: Path) -> None:
    """The epic's own feature bookkeeping proves it's actually fully implemented — this
    isn't a real external blocker, it's a QA-state bookkeeping desync (e.g. the epic was
    already QA-passed and its epic-level status/qa_passed got reverted or lost — see
    tempa_config.save_config's non-atomic-write caveat). Repair it and route it back through
    the normal QA gate instead of failing it.

    `rounds` is this epic's own `no_progress_rounds`, not the configured threshold — the two
    agree today and the message is trying to state the first."""
    _repair_qa_state_desync(epic)
    epic.pop("cut_short_rounds", None)
    save_config(config)
    log(
        f"Session [{session_label}] made no progress for {rounds} resumed session(s) "
        "in a row, but its own feature bookkeeping shows all "
        f"{epic.get('total_features', 0)} feature(s) are actually done — this looks "
        "like a QA-state bookkeeping desync (the epic was likely already QA-passed and "
        "its epic-level status/qa_passed got reverted or lost) rather than a real "
        "blocker outside this epic. Routing it back through the QA gate automatically "
        "instead of marking it failed; it will be re-QA'd on the next poll. Its own "
        f"last explanation:\n{reason}"
    )
    notify_attention(
        AttentionEventType.IMPLEMENTATION_QA_STATE_REPAIRED,
        "Implementation",
        f"{session_label} reports fully complete but wasn't marked done/QA-passed — "
        "routed back through QA",
        "No action needed — this was repaired automatically and will be re-QA'd on "
        "the next poll. If this recurs for the same epic, it's worth investigating "
        "why its QA/epic-level state keeps reverting (a config.json write race is "
        "one candidate).",
        epic=session_label,
        log_path=log_path,
        details={
            "reason": reason,
            "completed_features": epic.get("completed_features", 0),
            "total_features": epic.get("total_features", 0),
        },
    )


def _waiting_close_notice(reason: str, halts: int) -> str:
    """What the dashboard's Halted panel shows for an epic whose rounds keep ending on a promise
    to come back.

    That panel renders `blocked_reason` verbatim and nothing else, so on EPIC-02 a human opening
    a halted epic read: "I'm waiting for the full failure-list test run (background) to complete
    ... I'll report back once it finishes." That answers no question the panel is asking, names no
    blocker, and reads as though the run were still going. Everything the reader actually needed
    was known to Tempa and printed nowhere: that the session had already ended, that Tempa itself
    terminated what it was waiting for, and that this epic is blocked on nothing at all.

    So Tempa says that part in its own voice and quotes the session UNDERNEATH it rather than
    instead of it. The quoting is not politeness. EPIC-02's first round closed with "I've
    completed the code changes and live verification for all three features this session
    (FEAT-02-02, FEAT-02-04, FEAT-02-05)" — the only record anywhere that the work happened, since
    the round ended before writing it into config.json. A filter that suppressed that sentence for
    being a statement of waiting would have destroyed the most valuable line in the incident.

    `halts` escalates the text, and it is the reason `ended_waiting_halts` survives
    `reset_failed_epics`. Continue Implementation runs `--reset-failed` itself before every pass,
    which zeroes every counter and restarts the QA loop guard's window — so a panel that told the
    reader nothing needed fixing would be a rubber stamp on an unbounded loop, with nothing on
    either side of the screen distinguishing the first halt of this shape from the tenth. The
    first one says what happened. The second says it has now happened twice across two resets,
    and names the one number that tells an interrupted epic from a stuck one."""
    repeat = ""
    if halts > 1:
        repeat = (
            f"\n\nThis is the {halts}th time this epic has halted this way, across {halts} "
            "reset(s). Once is an interruption. Twice is a pattern, and the likelier reading is "
            "that these rounds cannot finish for a reason none of them has managed to state. "
            "Check whether \"completed_features\" on this epic has moved at all across those "
            "halts: if it has not, this needs looking at rather than another retry."
        )
    return (
        "This round did not report a blocker — it ended by promising to come back with a result "
        "later, and it could not. A Tempa session is one headless CLI run: writing that sentence "
        "ended the turn, ending the turn ended the run, and Tempa's own cleanup then terminated "
        "the process(es) the session had left running.\n\n"
        "So read this epic's counters as understating it. Whatever that round says it finished "
        "was never written into config.json, because it ended before that step, and whatever it "
        "was waiting on produced nothing anywhere. Nothing here says the epic is blocked on "
        "another epic or on a decision from you — the next round has to redo that check itself, "
        f"narrowed so it completes inside one command.{repeat}\n\n"
        f"The session's own closing words, in full:\n{reason}"
    )


def _no_closing_words(log_path: Path) -> str:
    """What to record when a stalled round left nothing quotable at all.

    `blocked_reason` is assigned unconditionally on this path, unlike the `last_round_note` write
    a few lines above it, so a round that produced no prose and whose log could not be read stored
    "". The dashboard renders its Halted panel only when that field is truthy and `tempa status`
    skips its block for the same reason — so the epic showed as a bare red ✗ with no explanation
    and, because the retry hint rides on this same string, no way out anywhere on the page. That
    is the exact defect `with_retry_hint` was added to fix, reachable again through the one branch
    that never checked."""
    return (
        "This round left no closing explanation of its own, so there is nothing to quote here. "
        f"Its log has everything it did: {log_path.name}"
    )


def _fail_blocked_epic(config: dict, epic: dict, session_label: str, reason: str,
                       blocked_by_epic: str | None, reorder_failure: str, log_path: Path,
                       *, ended_waiting: bool = False) -> None:
    """Nothing could be fixed automatically: stop resuming the epic, record why, and stop the
    runner for a human to look at.

    What this is allowed to CLAIM depends on what the session actually said. When it named a
    `blocked_by_epic` and the reorder was refused, "very likely blocked on something outside this
    epic" is that session's own report and repeating it is fair. When it named nothing, the
    sentence went out anyway — and on EPIC-02 it was simply untrue: that epic was blocked on
    nothing, its rounds just kept ending before they recorded anything. Note that the very next
    line of the same message already conceded it, verbatim: "Could not fix this automatically by
    reordering: the session didn't name a specific epic it's blocked on." The runner admitted it
    had no evidence one line under the claim, and the claim is the part a human reads first — it
    sends them hunting a dependency that does not exist. So the unnamed case now states only what
    is known, and the cut-short case states what Tempa itself did.

    `ended_waiting` says the round closed on a promise to come back whose object Tempa terminated
    (see `_ended_waiting_on_killed_work`). `reason` stays the session's words alone everywhere
    they are offered as such, so every line that calls them "its own last explanation" still tells
    the truth; only what is STORED is Tempa's account with those words quoted inside it."""
    if ended_waiting:
        epic["ended_waiting_halts"] = epic.get("ended_waiting_halts", 0) + 1
        halt_text = _waiting_close_notice(reason, epic["ended_waiting_halts"])
    else:
        halt_text = reason or _no_closing_words(log_path)
    halt_text = _reason_with_counterpart_context(halt_text, config["epic"], blocked_by_epic)
    epic["blocked_reason"] = with_retry_hint(halt_text)
    epic["status"] = "failed"
    save_config(config)
    if ended_waiting:
        log(f"Session [{session_label}] completed no feature in "
            f"{epic['no_progress_rounds']} resumed session(s) in a row, and ended by saying it "
            "would come back with a result once something it had started in the background "
            "finished. It could not: a session here is one headless CLI run, so the turn that "
            "said it ended the run, and the container teardown terminated what was left running. "
            "That is not evidence this epic is blocked — it is evidence its rounds keep ending "
            "before they record anything, and it has already been given a spare round for it. "
            f"Marking it failed rather than resuming it into the same shape.\n{halt_text}")
        title = f"{session_label} keeps ending its rounds waiting on work Tempa had to terminate"
        guidance = ("Read what Tempa recorded below before retrying — it says what happened and "
                    "what to check. Then run Continue Implementation.")
    elif blocked_by_epic is None:
        log(f"Session [{session_label}] completed no feature in "
            f"{epic['no_progress_rounds']} resumed session(s) in a row and never named an epic "
            "it is blocked on, so there is nothing to reorder and nothing here establishes that "
            "the blocker is outside this epic at all. Marking it failed rather than resuming it "
            f"indefinitely. Its own last explanation:\n{reason}\n"
            "Resolve whatever that turns out to be, then run `tempa implement --reset-failed`.")
        title = (f"{session_label} made no progress across {epic['no_progress_rounds']} rounds "
                 "— no blocker named")
        guidance = ("Nothing here says what it is blocked on. Read the reason below, resolve "
                    "whatever it turns out to be, then run `tempa implement --reset-failed`.")
    else:
        log(
            f"Session [{session_label}] made no progress for {epic['no_progress_rounds']} "
            "resumed session(s) in a row — it's very likely blocked on something outside "
            "this epic rather than still genuinely working. Marking it failed instead of "
            f"continuing to resume it. Its own last explanation:\n{reason}\n"
            f"Could not fix this automatically by reordering: {reorder_failure}.\n"
            "Resolve the blocker, then run `tempa implement --reset-failed`."
        )
        title = f"{session_label} made no progress and is likely blocked"
        guidance = ("Review the reason below, resolve the blocker, then run "
                    "`tempa implement --reset-failed`.")
    notify_attention(
        AttentionEventType.IMPLEMENTATION_FAILED,
        "Implementation",
        title,
        guidance,
        epic=session_label,
        log_path=log_path,
        details={"reason": halt_text, "no_progress_rounds": epic["no_progress_rounds"],
                 "reorder_failure": reorder_failure,
                 "ended_waiting_halts": epic.get("ended_waiting_halts", 0)},
    )
    _state.stop_event.set()


def _defer_for_human_decision(config: dict, index: int, session_label: str,
                              waiting: list[dict], log_path: Path) -> None:
    """`waiting`'s features are all this epic has left, and each needs an answer only a human can
    give — so park the epic and let the runner carry on with the rest of the plan.

    Deliberately NOT a failure and deliberately not `_state.stop_event`: nothing is wrong, and the
    later epics that don't touch this question have no reason to wait for it. `deferred` is not a
    status `_start_next_epic` picks up, so the epic simply sits out until an answer is written
    (`_resume_answered_decisions` in tempa_implement puts it straight back)."""
    epic = config["epic"][index]
    epic["status"] = EPIC_DEFERRED
    questions = "\n".join(describe(feature) for feature in waiting)
    hint = answer_hint(str(get_config_path()), epic.get("epic_name", session_label))
    epic["blocked_reason"] = f"{questions}\n\n{hint}"
    save_config(config)
    plural = "feature" if len(waiting) == 1 else "features"
    log(f"Session [{session_label}] has nothing left it can finish on its own — its last "
        f"{len(waiting)} {plural} need a decision only you can make. Deferring this epic and "
        f"continuing with the rest of the plan instead of stopping the run.\n{questions}\n{hint}")
    notify_attention(
        AttentionEventType.IMPLEMENTATION_DECISION_REQUIRED,
        "Implementation",
        f"{session_label} needs a decision on {len(waiting)} {plural}",
        hint,
        epic=session_label,
        log_path=log_path,
        details={"blocked_features": ", ".join(f.get("id", "?") for f in waiting),
                 "questions": questions},
    )


def _handle_stalled_round(config: dict, index: int, session_label: str, completed_before: int,
                          log_path: Path) -> None:
    """An implementation session that exited 0 — which says nothing on its own about whether
    the epic moved forward. Track the round, and once `implement_no_progress_rounds` of them
    have gone by without another feature completing, work out which of the three things it
    actually is: an out-of-order dependency (reorder), a QA-state bookkeeping desync (repair),
    or a genuine external blocker (fail)."""
    epic = config["epic"][index]
    if epic["status"] not in ("on_progress", "require_fixing"):
        return

    # Checked before the stall counter, not after it: once the only work left is a question for a
    # human, resuming the epic twice more to confirm that is pure waste — and the round that
    # declares the block completes no feature, so it would otherwise be counted as a stall and
    # eventually reported as an epic that failed rather than one that is waiting on you.
    waiting = blocked_features(epic)
    if waiting and not has_other_work(epic):
        _defer_for_human_decision(config, index, session_label, waiting, log_path)
        return

    limit = config.get("implement_no_progress_rounds", 2)
    stalled = _update_no_progress_tracking(epic, completed_before, limit)
    reason = _last_meaningful_log_lines(log_path)
    ended_waiting = _ended_waiting_on_killed_work()
    # `no_progress_rounds` is 0 here iff the round just completed a feature, in which case
    # _update_no_progress_tracking has already dropped the previous note and this round has
    # nothing to explain — writing one back would resurrect it a line after it was cleared.
    if reason and epic.get("no_progress_rounds", 0) > 0:
        # Recorded on EVERY stalled round, not only the one that trips the limit, and read back
        # into the next round's prompt (see _last_round_note_block in tempa_prompts). A session
        # that works out why it can't finish has, until now, had nowhere to put that: it goes into
        # the closing message, the runner reads six lines of it for the human, and the next round
        # starts over. Four consecutive rounds were once spent re-deriving the same conclusion,
        # each ending in a longer restatement of it than the last.
        epic["last_round_note"] = reason
        # The note travels with what KIND of thing it is. Same text, two meanings, and the prompt
        # can frame only one of them: a conclusion about the code is a claim the next round should
        # check, while a promise to come back is an intention whose object Tempa has already
        # destroyed. Handing the second over under the first's framing is not a wording problem —
        # round 2 of the incident was handed round 1's "I'm now waiting for the full
        # Configuration.Tests suite... I'll report back once it completes." as a CLAIM TO CHECK,
        # under the instruction to check it against the code as it stands now
        # (process_20260818_150522.txt:760). It did exactly that: it re-ran the suite, for twelve
        # minutes, in the background, and ended the same way.
        if ended_waiting:
            epic["last_round_note_kind"] = LAST_ROUND_NOTE_UNFINISHED_CHECK
        else:
            epic.pop("last_round_note_kind", None)
    if not stalled:
        save_config(config)
        return
    epic["blocked_reason"] = reason or _no_closing_words(log_path)
    blocked_by_epic = epic.get("blocked_by_epic")
    reorder_failure = (
        _try_reorder_for_dependency(config, index, blocked_by_epic)
        if blocked_by_epic else "the session didn't name a specific epic it's blocked on"
    )
    rounds = epic.get("no_progress_rounds", 0)
    if reorder_failure is None:
        _requeue_reordered_epic(config, epic, session_label, blocked_by_epic, reason, rounds, log_path)
        return
    if _epic_genuinely_complete(epic):
        _requeue_for_qa_after_desync(config, epic, session_label, reason, rounds, log_path)
        return
    # Last, deliberately: both repairs above need no human and must keep firing on the round they
    # always have. Only the verdict that costs a human gets deferred by the grace.
    if ended_waiting and _bank_cut_short_grace(epic):
        save_config(config)
        log(f"Session [{session_label}] completed no feature for {rounds} resumed session(s) in "
            "a row, but this round ended by saying it would come back with a result once "
            "something it had started finished — and Tempa's own cleanup terminated what it had "
            "left running, so no such result was ever going to arrive. That is a round Tempa cut "
            "short, not evidence this epic is blocked, so it gets one more before the count is "
            "allowed to matter. The next one counts whatever happens.")
        return
    _fail_blocked_epic(config, epic, session_label, reason, blocked_by_epic,
                       reorder_failure, log_path, ended_waiting=ended_waiting)


def apply_session_outcome(
    index: int,
    session_label: str,
    exit_code: int,
    log_path: Path,
    completed_before: int,
    backend: Backend,
) -> None:
    """Record what this finished implementation session means for epic `index`.

    Called by run_session while holding _state.lock (every branch here read-modify-writes
    config.json and may set _state.stop_event, so it must not race another session thread).
    Saves `config` itself on every branch that changes anything.
    """
    # A usage-limit, auth-error, server-overload, stuck-after-done, or background-work
    # termination is not a real epic failure: leave status untouched so the epic can be
    # resumed once the limit resets / auth is fixed / the backend's API recovers / next
    # time around. The stuck-after-done case in particular already did its real work (it
    # had reached "[Done]" — its own signal that the turn, and whatever config.json
    # updates it makes, is complete) before getting stuck purely in unrelated process
    # cleanup; the forced termination's non-zero exit code doesn't mean the task itself
    # failed. A background-work termination is the mirror image — the session was cut
    # short BEFORE it could finish, which is equally not this epic's fault.
    if (
        exit_code != 0
        and not _state.usage_limit_hit
        and not _state.auth_error_hit
        and not _state.server_overloaded_hit
        and not _state.backend_stuck_after_done_hit
        and not _state.background_tasks_terminated_hit
    ):
        # Only mark failed — "done"/"pending" is set by the AI session itself
        config = load_config()
        config["epic"][index]["status"] = "failed"
        # A reason as well as the status: blocked_reason is the whole of what the dashboard's
        # Halted panel shows, so failing without one left the epic as a bare red ✗ with no
        # explanation and no next step anywhere on the Status tab.
        config["epic"][index]["blocked_reason"] = with_retry_hint(
            f"The implementation session exited with code {exit_code}. Its log has what went "
            f"wrong: {log_path.name}"
        )
        save_config(config)
        log(f"Session [{session_label}] marked as failed")
        notify_attention(
            AttentionEventType.IMPLEMENTATION_FAILED,
            "Implementation",
            f"{session_label} implementation failed",
            "Review the session log, correct the issue, then run `tempa implement --reset-failed`.",
            epic=session_label,
            log_path=log_path,
        )
        _state.stop_event.set()
    elif _state.background_tasks_terminated_hit:
        # The session never got to finish: the backend CLI killed the background work
        # the turn left running once its own wait ceiling expired (see
        # _handle_background_terminated, which already logged what happened) and exited.
        # completed_features therefore says nothing about whether this epic is blocked —
        # counting it as a no-progress round is how a productive session gets mistaken
        # for a stalled one and, after implement_no_progress_rounds of them, failed
        # outright. Leave the epic exactly as the session left it so the next poll
        # resumes it; max_session_run remains the backstop against a real loop here.
        log(f"Session [{session_label}] was cut short by {backend.label} terminating its "
            "own background work, so this round is left out of the no-progress count — "
            "the epic stays resumable and continues on the next poll.")
    elif exit_code == 0 and not (_state.usage_limit_hit or _state.auth_error_hit or _state.server_overloaded_hit):
        # The session finished "successfully" (exit 0) but that alone doesn't mean it made
        # progress — a backend that's genuinely blocked on something outside this epic (a
        # dependency owned by a not-yet-implemented epic, say) will explain that and exit 0
        # every time it's resumed. Without this, such an epic gets silently re-resumed every
        # poll_interval_sec until it burns all the way through max_session_run.
        _handle_stalled_round(load_config(), index, session_label, completed_before, log_path)
