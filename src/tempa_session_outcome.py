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
from tempa_config import load_config, save_config
from tempa_logging import _state, log
from tempa_maintenance import _epic_features_actually_done, with_retry_hint
from tempa_notifications import AttentionEventType, notify_attention


def _last_meaningful_log_lines(log_path: Path, max_lines: int = 6) -> str:
    """Return the last `max_lines` non-empty lines of `log_path`, dropping the trailing
    `[Done] input=... output=...` accounting line if present — i.e. the backend's own
    closing explanation of what it did/why it stopped, for surfacing to a human (e.g. the
    no-forward-progress guard in `_handle_stalled_round`) instead of only living in a log file."""
    try:
        lines = [line for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    if lines and lines[-1].startswith("[Done]"):
        lines = lines[:-1]
    return "\n".join(lines[-max_lines:])


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

    Mutates `epic` in place, mirroring how `total_run` is already tracked directly on it."""
    if epic.get("completed_features", 0) > completed_before:
        epic["no_progress_rounds"] = 0
        epic["total_run"] = 0
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
    history = config.setdefault("epic_reorder_history", [])
    if [stuck_name, blocked_by_epic] in history:
        return f"moving '{stuck_name}' before '{blocked_by_epic}' already happened previously — this looks like a circular dependency between the two"
    history.append([blocked_by_epic, stuck_name])
    epics.insert(stuck_index, epics.pop(target_index))
    return None


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
                            reason: str, limit: int, log_path: Path) -> None:
    """The blocker the session named was moved ahead of this epic in the plan — put this one
    back in the queue behind it. Not a failure: nothing needs a human."""
    epic["no_progress_rounds"] = 0
    epic["status"] = "pending"
    save_config(config)
    log(
        f"Session [{session_label}] made no progress for {limit} resumed session(s) "
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
                                 limit: int, log_path: Path) -> None:
    """The epic's own feature bookkeeping proves it's actually fully implemented — this
    isn't a real external blocker, it's a QA-state bookkeeping desync (e.g. the epic was
    already QA-passed and its epic-level status/qa_passed got reverted or lost — see
    tempa_config.save_config's non-atomic-write caveat). Repair it and route it back through
    the normal QA gate instead of failing it."""
    _repair_qa_state_desync(epic)
    save_config(config)
    log(
        f"Session [{session_label}] made no progress for {limit} resumed session(s) "
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


def _fail_blocked_epic(config: dict, epic: dict, session_label: str, reason: str,
                       blocked_by_epic: str | None, reorder_failure: str, log_path: Path) -> None:
    """Nothing could be fixed automatically: the epic is very likely blocked on something
    outside itself, so fail it and stop the runner for a human to look at."""
    reason = _reason_with_counterpart_context(reason, config["epic"], blocked_by_epic)
    epic["blocked_reason"] = with_retry_hint(reason)
    epic["status"] = "failed"
    save_config(config)
    log(
        f"Session [{session_label}] made no progress for {epic['no_progress_rounds']} "
        "resumed session(s) in a row — it's very likely blocked on something outside "
        "this epic rather than still genuinely working. Marking it failed instead of "
        f"continuing to resume it. Its own last explanation:\n{reason}\n"
        f"Could not fix this automatically by reordering: {reorder_failure}.\n"
        "Resolve the blocker, then run `tempa implement --reset-failed`."
    )
    notify_attention(
        AttentionEventType.IMPLEMENTATION_FAILED,
        "Implementation",
        f"{session_label} made no progress and is likely blocked",
        "Review the reason below, resolve the blocker, then run "
        "`tempa implement --reset-failed`.",
        epic=session_label,
        log_path=log_path,
        details={"reason": reason, "no_progress_rounds": epic["no_progress_rounds"],
                  "reorder_failure": reorder_failure},
    )
    _state.stop_event.set()


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
    limit = config.get("implement_no_progress_rounds", 2)
    if not _update_no_progress_tracking(epic, completed_before, limit):
        save_config(config)
        return
    reason = _last_meaningful_log_lines(log_path)
    epic["blocked_reason"] = reason
    blocked_by_epic = epic.get("blocked_by_epic")
    reorder_failure = (
        _try_reorder_for_dependency(config, index, blocked_by_epic)
        if blocked_by_epic else "the session didn't name a specific epic it's blocked on"
    )
    if reorder_failure is None:
        _requeue_reordered_epic(config, epic, session_label, blocked_by_epic, reason, limit, log_path)
    elif _epic_genuinely_complete(epic):
        _requeue_for_qa_after_desync(config, epic, session_label, reason, limit, log_path)
    else:
        _fail_blocked_epic(config, epic, session_label, reason, blocked_by_epic,
                           reorder_failure, log_path)


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
