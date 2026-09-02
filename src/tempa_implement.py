"""The implement pipeline: the poll loop, the top-level `main` entry, and plan-epics.

`main` decides whether to run plan first (no pending work, or --replan) then loops calling
`check_and_run` every config.json poll_interval_sec seconds. `check_and_run` is the scheduler: it picks
the single next thing to do (resume QA, resume an on_progress epic, gate QA, implement the
next require_fixing/pending epic) and starts it on a daemon thread, guarded by _state.lock.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

from tempa_backend import get_backend_def
from tempa_config import (
    WORKING_DIR,
    clear_graceful_stop,
    get_backend,
    get_config_path,
    get_epic_session_id,
    get_model,
    get_poll_interval_sec,
    get_qa_dir,
    get_reasoning_effort,
    get_resume_implementation_sessions,
    get_sources,
    graceful_stop_requested,
    load_config,
    save_config,
)
from tempa_decisions import (
    EPIC_DEFERRED,
    answered_features,
    apply_pending_answers,
    blocked_features,
    describe,
)
from tempa_logging import _banner, _init_process_log, _state, log, process_log_path
from tempa_maintenance import (
    _epic_features_actually_done,
    reconcile_qa_passed_features_and_log,
    reset_failed_epics,
    with_retry_hint,
)
from tempa_notifications import AttentionEventType, flush_pending_notifications, notify_attention
from tempa_prompts import (
    build_plan_epics_prompt,
    build_qa_prompt,
    build_review_epics_prompt,
    build_session_prompt,
)
from tempa_session import (
    run_with_usage_limit_retry,
    wait_out_server_overload,
    wait_out_usage_limit,
)
from tempa_session_runners import _run_oneshot_session, run_qa_session, run_session


def _validate_and_increment_run(config: dict, index: int, label: str) -> bool:
    """Increment total_run and validate against max_session_run. Returns False if limit exceeded.

    On the limit path, also marks the epic `failed` and saves `config` — without this it was
    left stuck in `on_progress` forever with no self-service way out: `--reset-failed` only
    resets epics whose status is already `failed`, and clicking Continue Implementation would
    just hit this exact same check again on every future attempt. Marking it failed here means
    the very next `tempa implement --reset-failed` (which the dashboard's Continue
    Implementation button already runs automatically before every implement pass) resets it —
    see reset_failed_epics, which also clears total_run/no_progress_rounds for a clean retry."""
    max_run = config.get("max_session_run")
    total_run = config["epic"][index].get("total_run", 0)
    if max_run is not None and total_run >= max_run:
        log(f"Session [{label}] has reached the max_session_run limit ({max_run}). "
            "Marking it failed — run `tempa implement --reset-failed` (or just click Continue "
            "Implementation again, which does this automatically) to reset it and retry.")
        config["epic"][index]["status"] = "failed"
        config["epic"][index]["blocked_reason"] = with_retry_hint(
            f"Reached the max_session_run limit ({max_run}): that many implementation sessions "
            "have run for this epic without it finishing. Raise max_session_run if the epic is "
            "simply large, or look at why its sessions aren't completing it."
        )
        save_config(config)
        notify_attention(
            AttentionEventType.SESSION_LIMIT_REACHED, "Implementation",
            f"{label} reached its session limit",
            "Marked failed. Review the epic, then run `tempa implement --reset-failed` "
            "(or click Continue Implementation again) to reset it and retry.",
            epic=label, details={"max_session_run": max_run},
        )
        return False
    config["epic"][index]["total_run"] = total_run + 1
    return True


def _validate_and_increment_qa_run(config: dict, index: int, label: str) -> bool:
    """Increment qa_total_run and validate against max_session_run. Returns False if limit exceeded.

    On the limit path the epic is marked `failed`, NOT passed. This used to set
    qa_passed=True — declaring an epic QA-verified precisely because Tempa had run out of
    attempts to verify it, while the last real verdict on record was a failure. That is the one
    outcome a QA gate must never produce: the run continues, later epics build on it, and nothing
    downstream can tell it apart from an epic that genuinely passed.

    `qa_status` has to be cleared alongside the status. Leaving it "ongoing" would send the very
    next poll straight back into check_and_run's QA-resume branch, which runs before the
    failed-epic halt and would re-dispatch this same QA forever. And the stop_event is what keeps
    a failed LAST epic from falling through to "no next_index" → all_done → "All epics done" —
    the same reason run_session sets it on its own failure path."""
    max_run = config.get("max_session_run")
    qa_total_run = config["epic"][index].get("qa_total_run", 0)
    if max_run is not None and qa_total_run >= max_run:
        log(f"QA [{label}] has reached the max_session_run limit ({max_run}) without ever passing. "
            "Marking it failed rather than passing it unverified — review the epic and its QA "
            "reports, then run `tempa implement --reset-failed` (or click Continue Implementation) "
            "to retry.")
        config["epic"][index]["status"] = "failed"
        config["epic"][index]["qa_status"] = "idle"
        config["epic"][index]["qa_passed"] = False
        config["epic"][index]["blocked_reason"] = with_retry_hint(
            f"QA reached the max_session_run limit ({max_run}) without ever passing. Marked "
            "failed rather than passed unverified — this epic has never been QA-verified. "
            "Review its QA reports for what keeps failing."
        )
        notify_attention(
            AttentionEventType.QA_LIMIT_REACHED, "QA",
            f"{label} QA reached its session limit",
            "The epic was marked failed, not passed — it has never been verified. Review it and "
            "its QA reports, then run `tempa implement --reset-failed` to retry.",
            epic=label, details={"max_session_run": max_run},
        )
        _state.stop_event.set()
        return False
    config["epic"][index]["qa_total_run"] = qa_total_run + 1
    return True


def _halt_if_earlier_epic_failed(config: dict, upto_index: int) -> None:
    """Raise SystemExit(1) if any epic before `upto_index` is `failed` — nothing after a failed
    epic may be worked on until a human resolves it.

    Called from BOTH places that pick the next thing to do. The QA gate needs it because its own
    "wait for the previous epic's re-implementation" deferral (see check_and_run) matches any
    earlier epic with qa_status="done" + qa_passed=false — which is exactly the state a `failed`
    epic is left in when its QA found issues and the fix session, the QA run limit, or the QA
    loop guard subsequently gave up on it. That deferral never resolves on its own, so without
    this the runner logs "QA [x] deferred" on every poll forever and never reaches the halt below
    it, turning a stop that should be actionable into a silent spin."""
    for i in range(upto_index):
        if config["epic"][i].get("status") == "failed":
            label = config["epic"][i].get("epic_name", f"epic_{i}")
            log(f"Halted — session [{label}] at index {i} has failed. Fix it, then run "
                "`tempa implement --reset-failed` (failed → pending) before proceeding.")
            raise SystemExit(1)


def _reset_failed_before_retry(label: str) -> None:
    """Clear a leftover `failed` epic status before an automatic retry resumes work —
    the in-process equivalent of `tempa implement --reset-failed`.

    A session cut short by the backend's API reporting itself overloaded (Anthropic's
    transient 529) can still end up marked `failed` in config.json: run_session only skips
    that marking while `_state.server_overloaded_hit` is set, i.e. only when the overload
    was actually recognized in the streamed output, so an overload that surfaces in some
    other wording — or that kills the CLI again on the very next attempt — looks like a
    plain non-zero exit. Once `failed` is on disk it is sticky and fatal: check_and_run
    halts on any failed epic preceding the next one to work on ("Halted — session [x] at
    index i has failed"), so every later poll and every later `tempa implement` run would
    fail the same way until someone reset it by hand. An overload is not a real failure, so
    reset it here instead and let the retry pick the epic back up."""
    with _state.lock:
        config = load_config()
        reset = reset_failed_epics(config)
        if not reset:
            return
        save_config(config)
    log(f"{label} — reset {len(reset)} epic(s) left as failed by the interrupted session "
        f"back to pending before retrying ({', '.join(reset)}); same as "
        "`tempa implement --reset-failed`.")


def _await_session_thread(timeout_sec: float = 120.0) -> None:
    """Let the in-flight session thread finish its own post-session bookkeeping before main()
    reads (and clears) the `_state.*_hit` flags.

    Every stop signal — usage limit, auth error, overload, stuck-after-done — is raised from
    inside the session thread's own stack together with `_state.stop_event`, and that wakes
    main()'s poll loop while `run_session` is still seconds away from acting on it: it has to
    drain stdout (see _STDOUT_DRAIN_GRACE_SEC) and then process.wait(). Clearing
    `backend_stuck_after_done_hit` inside that window is what turned a deliberately-not-a-
    failure into a runner stop: run_session then saw a plain non-zero exit, marked the epic
    `failed`, and re-set stop_event, so the next loop turn found no flags left and exited 1
    (seen live — force-terminate logged at 03:24:21, "marked as failed" at 03:24:24, exactly
    the 3s stdout-drain grace apart). Joining first means the session thread and main() always
    agree on which flags were set.

    Bounded, so a genuinely wedged session thread can't hang the runner forever."""
    thread = _state.running_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout_sec)


# How often the poll loop wakes while a graceful stop is pending. The normal poll interval
# is 60s by default, which would leave the runner idling for up to a minute after the
# session it was waiting on already finished — nothing is being spent in that window, but
# the dashboard sits on "Stopping after current session…" for no reason.
_GRACEFUL_STOP_POLL_SEC = 5


def _graceful_stop_is_due() -> bool:
    """Whether the user's graceful stop can be honoured right now — i.e. one was requested
    AND no session thread is still running.

    The whole point of a graceful stop is that the session in progress gets to finish and
    record its work, so this deliberately reports False (keep waiting) for as long as one
    is alive, however long that takes. The immediate Stop is still there for anyone who
    doesn't want to wait."""
    if not graceful_stop_requested("implement"):
        return False
    with _state.lock:
        thread = _state.running_thread
        return thread is None or not thread.is_alive()


def _resume_interrupted_qa(config: dict) -> bool:
    """Highest scheduling priority: a QA session that was cut off mid-run (qa_status
    "ongoing") is resumed before anything else. Returns True once it has started one."""
    for i, session in enumerate(config["epic"]):
        if session.get("qa_status") == "ongoing":
            label = session.get("epic_name", f"epic_{i}")
            resume_sid = get_epic_session_id(session, get_backend(config, "implement"), kind="qa")
            log(f"QA [{label}] was interrupted (qa_status=ongoing) — resuming with session_id: {resume_sid}")

            if not _validate_and_increment_qa_run(config, i, label):
                save_config(config)
                return True

            qa_dir = get_qa_dir()
            qa_dir.mkdir(parents=True, exist_ok=True)
            qa_report_filename = session.get("qa_report_filename", "")
            if qa_report_filename:
                qa_output_file = Path(qa_report_filename)
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                qa_output_file = qa_dir / f"{label}-qa-{timestamp}.md"
                config["epic"][i]["qa_report_filename"] = str(qa_output_file)

            prompt = build_qa_prompt(config, label, qa_output_file, is_continuation=True)
            save_config(config)

            _state.running_index = i
            _state.running_thread = threading.Thread(
                target=run_qa_session,
                args=(i, prompt, label, resume_sid),
                daemon=True,
            )
            _state.running_thread.start()
            return True

    return False


def _resume_in_progress_epic(config: dict, features_override: int | None) -> bool:
    """An epic left "on_progress" by an interrupted session (process killed, machine
    restarted): always start another session for it — resuming the previous one when
    there's a session id to resume. Returns True once it has started one."""
    for i, session in enumerate(config["epic"]):
        if session["status"] == "on_progress":
            label = session.get("epic_name", f"epic_{i}")

            total = session.get("total_features", 0)
            completed = session.get("completed_features", 0)
            progress_str = f"{completed}/{total}" if total else "?"
            # A stale on_progress session (the previous session was interrupted —
            # process killed, machine restarted, etc.) still gets a resumable
            # session_id captured on the epic if one was ever produced (see
            # _capture_session_id / run_session) — resume it instead of starting
            # cold and re-reading the epic spec + code again, same reasoning as the
            # continuation case below.
            resume_sid = (
                get_epic_session_id(session, get_backend(config, "implement"), kind="implement")
                if get_resume_implementation_sessions(config) else None
            )
            log(f"Session [{label}] progress: {progress_str} features — "
                f"{'resuming' if resume_sid else 'starting'} a session")

            prompt = build_session_prompt(
                config, session.get("epic_name", ""),
                is_continuation=completed > 0, features_override=features_override,
                is_resumed=bool(resume_sid),
            )

            if not _validate_and_increment_run(config, i, label):
                raise SystemExit(1)

            config["epic"][i]["qa_passed"] = False
            config["epic"][i]["qa_status"] = "idle"
            config["epic"][i]["last_run"] = datetime.now().isoformat()
            save_config(config)

            _state.running_index = i
            _state.running_thread = threading.Thread(
                target=run_session,
                args=(i, prompt, label, resume_sid),
                kwargs={"features_override": features_override},
                daemon=True,
            )
            _state.running_thread.start()
            return True

    return False


def _run_qa_gate(config: dict) -> bool:
    """The QA gate: the first "done" epic that hasn't passed QA yet gets QA'd before any
    later epic is worked on. Returns True if this poll is spoken for — which includes the
    cases where nothing is started: an epic routed back to implementation because its
    feature bookkeeping contradicts its "done" status, a QA run deferred behind an earlier
    epic's re-implementation, and a QA run limit reached."""
    for i, session in enumerate(config["epic"]):
        if session["status"] == "done" and not session.get("qa_passed", False):
            label = session.get("epic_name", f"epic_{i}")

            # Integrity check: don't trust the epic-level "done" status blindly — the AI
            # agent is solely responsible for also marking each feature "done" and
            # incrementing completed_features before setting the epic itself done (see
            # the MANDATORY RULE in build_session_prompt), and it can skip that step. A
            # "done" epic whose features were never actually finished must be fixed for
            # real before QA runs on it, not QA'd (and possibly passed) against
            # incomplete work.
            if not _epic_features_actually_done(session):
                completed = session.get("completed_features", 0)
                total = session.get("total_features", len(session.get("features", [])))
                log(f"[{label}] is marked done but only {completed}/{total} feature(s) "
                    "are actually marked done — routing back to implementation before "
                    "QA runs (a re-implementation round likely set the epic done without "
                    "finishing each feature's own bookkeeping).")
                config["epic"][i]["status"] = "require_fixing"
                save_config(config)
                return True

            # A failed earlier epic is never going to finish that re-implementation on its
            # own, so it must halt here rather than fall into the deferral below.
            _halt_if_earlier_epic_failed(config, i)

            # Block if any PREVIOUS epic's QA found issues and is waiting for re-implementation.
            # qa_status="done" + qa_passed=false means QA ran and failed — that epic must be
            # re-implemented (and re-QA'd) before we advance to this one.
            #
            # A `deferred` epic matches that same shape (QA ran, found the finding nobody can
            # close without a decision) but will NOT re-implement on its own — it is waiting on a
            # human, which is precisely why the runner was allowed to move past it. Deferring
            # every later epic's QA behind it would spin "QA [x] deferred" on every poll forever
            # and undo the point of deferring at all, the same trap _halt_if_earlier_epic_failed
            # exists to catch for `failed`.
            blocked_by = next(
                (config["epic"][j].get("epic_name", f"epic_{j}")
                 for j in range(i)
                 if not config["epic"][j].get("qa_passed", False)
                 and config["epic"][j].get("qa_status") not in ("idle", None)
                 and config["epic"][j].get("status") != EPIC_DEFERRED),
                None,
            )
            if blocked_by:
                log(f"QA [{label}] deferred — waiting for [{blocked_by}] re-implementation + QA to finish first")
                return True

            log(f"QA is required for [{label}] before continuing implementation")

            if not _validate_and_increment_qa_run(config, i, label):
                save_config(config)
                return True

            qa_dir = get_qa_dir()
            qa_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            qa_output_file = qa_dir / f"{label}-qa-{timestamp}.md"

            config["epic"][i]["qa_status"] = "ongoing"
            config["epic"][i]["qa_session_id"] = ""
            config["epic"][i]["qa_report_filename"] = str(qa_output_file)
            prompt = build_qa_prompt(config, label, qa_output_file)
            save_config(config)

            _state.running_index = i
            _state.running_thread = threading.Thread(
                target=run_qa_session,
                args=(i, prompt, label),
                daemon=True,
            )
            _state.running_thread.start()
            return True

    return False


def _resume_answered_decisions(config: dict) -> bool:
    """Put every epic whose deferred question has been answered back into the implementation
    queue, and return whether anything changed (the caller saves).

    This is the whole "recovery" story for a deferred epic: write the decision into the feature's
    `blocked_answer` and the next poll picks it up — no command to remember, no status to reset by
    hand. The answer itself is not consumed here; it stays on the feature so the session that
    picks the epic up is handed it (see `_blocked_feature_block` in tempa_prompts), and so the
    record of what was decided, and why, outlives the round that acted on it.

    Runs before any scheduling decision reads a status, so an answer written while the runner was
    idle takes effect on the very next poll rather than the one after."""
    changed = False
    for epic in (config.get("epic") or []):
        answered = answered_features(epic)
        # Keyed on "nothing is waiting on the user any more" rather than on "something was
        # answered", because those are not the same set. A blocked feature can also leave the
        # queue by being dropped outright — status set to `done`, which answer_hint documents
        # and the dashboard's Drop option does — and a dropped feature is no longer `blocked`,
        # so answered_features() cannot see it. Without this the epic stayed `deferred` forever,
        # skipped by the scheduler and the QA gate alike, with nothing left to answer.
        nothing_left_waiting = epic.get("status") == EPIC_DEFERRED and not blocked_features(epic)
        if not answered and not nothing_left_waiting:
            continue
        for feature in answered:
            feature["status"] = "require_fixing"
        # Back to require_fixing rather than the status it held when it was deferred: the epic has
        # a feature that is implemented-but-not-finished and a decision to apply, which is exactly
        # what require_fixing means, and it's the status _start_next_epic prioritises.
        if epic.get("status") == EPIC_DEFERRED:
            epic["status"] = "require_fixing"
        epic.pop("blocked_reason", None)
        # A fresh grace period, for the same reason _repair_qa_state_desync gives itself one: the
        # rounds counted before the epic was parked were spent on a question that has now been
        # answered. Carrying that count over means an epic deferred at no_progress_rounds=1 gets
        # exactly one round to act on the decision before the stall guard fails it.
        epic["no_progress_rounds"] = 0
        changed = True
        label = epic.get("epic_name", "?")
        if answered:
            ids = ", ".join(f.get("id", "?") for f in answered)
            log(f"[{label}] the decision it was waiting on has been answered ({ids}) — back in "
                "the implementation queue; the answer is handed to the session that picks it up.")
        else:
            log(f"[{label}] has nothing left waiting on a decision — back in the implementation "
                "queue.")
    return changed


def _log_deferred_epics_on_stop(config: dict) -> bool:
    """Report every epic parked on an unanswered decision when the runner runs out of work.

    Returns whether there were any — the caller uses it to keep "All epics done" honest. A run
    that ends with questions outstanding has NOT finished the plan, and saying so at the point the
    process exits is the difference between a decision that gets made and one that is discovered
    weeks later."""
    deferred = [e for e in (config.get("epic") or []) if e.get("status") == EPIC_DEFERRED]
    if not deferred:
        return False
    for epic in deferred:
        label = epic.get("epic_name", "?")
        questions = "\n".join(describe(f) for f in blocked_features(epic))
        log(f"[{label}] is still waiting on a decision from you:\n{questions}")
    log(f"{len(deferred)} epic(s) are deferred pending your decision — everything else in the "
        f"plan is done. Answer in {get_config_path()} (each blocked feature's \"blocked_answer\" "
        "field), then run `tempa implement` again to pick them back up.")
    return True


def _start_next_epic(config: dict, features_override: int | None) -> None:
    """Last resort: pick the next epic to implement — any "require_fixing" one first (already
    implemented, waiting on QA fixes), otherwise the first "pending" one — and start a session
    for it. Stops the runner instead if there is nothing left to do."""
    next_index = None
    for i, session in enumerate(config["epic"]):
        if session["status"] == "require_fixing":
            next_index = i
            break

    # If no require_fixing, find first pending epic
    if next_index is None:
        for i, session in enumerate(config["epic"]):
            if session["status"] == "pending":
                next_index = i
                break

    if next_index is None:
        _state.all_done = True
        if not _log_deferred_epics_on_stop(config):
            log("All epics done — agent runner stopping.")
        _state.stop_event.set()
        return

    # All epics before next_index must not be failed
    _halt_if_earlier_epic_failed(config, next_index)

    session = config["epic"][next_index]
    label = session.get("epic_name", f"epic_{next_index}")
    is_require_fixing = session["status"] == "require_fixing"
    is_continuation = is_require_fixing or session.get("completed_features", 0) > 0
    # Resume the epic's previous implementation session when continuing it (never for
    # a brand-new epic's first session — there's no session_id to resume yet anyway)
    # so it doesn't re-pay to read the epic spec + code it already read. A
    # require_fixing epic's QA report is still read fresh every time regardless (see
    # _build_qa_report_section) — only the "re-read the spec" instruction is skipped.
    resume_sid = (
        get_epic_session_id(session, get_backend(config, "implement"), kind="implement")
        if is_continuation and get_resume_implementation_sessions(config) else None
    )
    prompt = build_session_prompt(
        config, session.get("epic_name", ""),
        is_continuation=is_continuation,
        features_override=features_override,
        is_resumed=bool(resume_sid),
    )

    if not _validate_and_increment_run(config, next_index, label):
        raise SystemExit(1)

    config["epic"][next_index]["qa_passed"] = False
    config["epic"][next_index]["qa_status"] = "idle"
    config["epic"][next_index]["status"] = "on_progress"
    config["epic"][next_index]["last_run"] = datetime.now().isoformat()
    save_config(config)

    _state.running_index = next_index
    _state.running_thread = threading.Thread(
        target=run_session,
        args=(next_index, prompt, label, resume_sid),
        kwargs={"features_override": features_override},
        daemon=True,
    )
    _state.running_thread.start()


def check_and_run(features_override: int | None = None) -> None:

    with _state.lock:
        if _state.running_thread is not None and _state.running_thread.is_alive():
            log("Session in progress — skipping poll", to_console=False)
            return

        config = load_config()

        # Self-heal any QA-passed epic whose feature bookkeeping still contradicts its own
        # QA verdict (see reconcile_qa_passed_features) before anything reads that state to
        # make a scheduling decision. Also repairs configs written by older versions, which
        # had no such reconciliation at all.
        repaired = reconcile_qa_passed_features_and_log(config)
        # Decisions answered from outside this process (the dashboard, or a hand edit) are
        # re-applied first, so an answer that some other writer overwrote costs a poll interval
        # rather than costing the decision — see apply_pending_answers for why config.json alone
        # can't be trusted to hold it.
        applied = apply_pending_answers(config)
        # Answers written into a deferred epic's blocked feature put it back in the queue before
        # anything below reads a status to schedule on. Evaluated separately from `repaired`
        # rather than in one `or`, which would skip it whenever a repair had already happened.
        resumed = _resume_answered_decisions(config)
        if repaired or applied or resumed:
            save_config(config)

        # Scheduling priority, highest first: each step returns True once this poll is
        # spoken for. The order is the policy — resuming interrupted work always beats
        # starting new work, and no epic is implemented past one still waiting on QA.
        if _resume_interrupted_qa(config):
            return
        if _resume_in_progress_epic(config, features_override):
            return
        if _run_qa_gate(config):
            return
        _start_next_epic(config, features_override)


def _print_session_plan(config: dict, features_override: int | None = None) -> None:
    """Print the single epic and features that will be processed in this session."""
    epics = (config.get("epic") or [])

    _banner("THIS SESSION WILL PROCESS")

    # QA gate check: on_progress takes priority, then QA, then pending
    on_progress = next((e for e in epics if e["status"] == "on_progress"), None)
    if on_progress is None:
        qa_pending = [e for e in epics if e["status"] == "done" and not e.get("qa_passed", False)]
        if qa_pending:
            print("  [QA] QA STEP IS REQUIRED BEFORE THE NEXT IMPLEMENTATION", flush=True)
            for e in qa_pending:
                print(f"    [QA--] {e.get('epic_name', '?')} — {e.get('completed_features', 0)}/{e.get('total_features', 0)} features", flush=True)
            print(f"  QA will be run for: {qa_pending[0].get('epic_name', '?')}", flush=True)
            return

    # Mirror check_and_run priority: on_progress → require_fixing → pending
    target = on_progress
    if target is None:
        target = next((e for e in epics if e["status"] == "require_fixing"), None)
    if target is None:
        target = next((e for e in epics if e["status"] == "pending"), None)

    if target is None:
        print("  No epic needs processing. Everything is done.", flush=True)
        return

    features_per_session = features_override if features_override is not None else config.get("features_per_session")

    epic_name = target.get("epic_name", "?")
    status = target["status"]
    total_f = target.get("total_features", 0)
    completed_f = target.get("completed_features", 0)

    status_tag = {"on_progress": "[ON PROGRESS]", "require_fixing": "[REQUIRE FIXING]"}.get(status, "[PENDING]")
    print(f"  {status_tag} {epic_name} — {completed_f}/{total_f} features done", flush=True)

    pending_features = [f for f in target.get("features", []) if f["status"] in ("pending", "require_fixing")]
    if not pending_features:
        print("    (no pending features)", flush=True)
    else:
        shown = pending_features[:features_per_session] if features_per_session else pending_features
        for feat in shown:
            feat_icon = "🔧" if feat["status"] == "require_fixing" else "⬜"
            print(f"    {feat_icon} {feat['id']} — {feat['name']}", flush=True)
        if features_per_session and len(pending_features) > features_per_session:
            remaining = len(pending_features) - features_per_session
            print(f"    ... (+{remaining} more feature(s) in the next session)", flush=True)

    if features_per_session:
        print(f"  (Max {features_per_session} feature(s) per session)", flush=True)

    # Not shown once the epic has passed QA: a report is written on every round now, a passing
    # one included (it carries that round's non-blocking advisory notes), so its mere existence
    # no longer means there is anything left to fix.
    qa_report_filename = target.get("qa_report_filename", "")
    if qa_report_filename and not target.get("qa_passed") and Path(qa_report_filename).exists():
        print(f"  ⚠ QA FINDINGS — must be fixed: {qa_report_filename}", flush=True)


def _has_pending_work(config: dict) -> bool:
    """True if there's any epic/feature/QA task still needing implementation work —
    mirrors the priority checks in check_and_run(). False means either no epics exist yet,
    or every epic is done and has passed QA — i.e. nothing left without generating a new plan."""
    epics = (config.get("epic") or [])
    if not epics:
        return False
    for e in epics:
        if e.get("qa_status") == "ongoing":
            return True
        if e.get("status") in ("on_progress", "require_fixing", "pending"):
            return True
        if e.get("status") == "done" and not e.get("qa_passed", False):
            return True
        # A deferred epic has no work the runner can hand to a session right now, but it is very
        # much unfinished plan. Reporting it as "nothing left" is what main() reads as "this plan
        # is spent, draft a new one" — so an epic parked on one unanswered question would get the
        # whole project re-planned out from under it.
        if e.get("status") == EPIC_DEFERRED:
            return True
    return False


def main(features_override: int | None = None, replan: bool = False) -> None:
    _init_process_log()
    flush_pending_notifications()
    # A stop is a request aimed at THIS run, so anything left over from a previous one
    # (a run that was killed outright before it could read the sentinel, a machine
    # restart) must not stop this one before it does any work. Safe to do here: the
    # `implement --reset-failed` pass the dashboard runs first never reaches main()
    # (see _dispatch_implement), so this can't wipe a request made during it — the
    # dashboard's own run-state flag covers that window.
    clear_graceful_stop("implement")

    config = load_config()
    if replan or not _has_pending_work(config):
        if replan:
            log("--replan given — running plan (lay out epic/feature/task) before implementation.")
        else:
            log("No task (epic/feature/QA) to work on — running plan automatically "
                "before implementation.")
        if not _plan_epics_run(config):
            if _state.backend_config_error_hit:
                sys.exit(3)
            log("Plan failed — agent runner stopping.")
            notify_attention(
                AttentionEventType.PLAN_FAILED, "Planning", "Plan generation failed",
                "Review the planning session log and source specifications, then rerun `tempa implement`.",
            )
            sys.exit(1)

    start_time = datetime.now()
    poll_interval_sec = get_poll_interval_sec(config)
    banner_parts = [
        f"Agent Runner started {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"dir={WORKING_DIR}",
        f"poll={poll_interval_sec}s",
    ]
    _plog = process_log_path()
    if _plog:
        banner_parts.append(f"log={_plog.name}")
    if features_override is not None:
        banner_parts.append(f"features/session={features_override}")
    _banner(" | ".join(banner_parts))

    _print_session_plan(load_config(), features_override)

    log(f"Agent runner started — working dir: {WORKING_DIR}", to_console=False)
    log(f"Poll interval: {poll_interval_sec}s | Config: {get_config_path()}", to_console=False)
    if features_override is not None:
        log(f"features_per_session override: {features_override}", to_console=False)

    usage_limit_retries = 0
    overload_retries = 0
    while True:
        while not _state.stop_event.is_set():
            # Checked before dispatching anything, so a graceful stop can only ever land
            # BETWEEN units of work — never mid-session. If a session is still running,
            # this is False and the loop just waits for it below.
            if _graceful_stop_is_due():
                _state.graceful_stop_hit = True
                _state.stop_event.set()
                break
            try:
                check_and_run(features_override=features_override)
            except SystemExit:
                raise
            except Exception as e:
                log(f"Unexpected error in check_and_run: {e}")

            # Re-read every cycle (load_config() never caches) so a Settings change to
            # poll_interval_sec reaches this already-running loop without a restart.
            interval = get_poll_interval_sec(load_config())
            if graceful_stop_requested("implement"):
                interval = min(interval, _GRACEFUL_STOP_POLL_SEC)
            _state.stop_event.wait(timeout=interval)

        # The session thread that raised this stop may still be finishing its own bookkeeping
        # — wait for it before reading any of the flags below (see _await_session_thread).
        _await_session_thread()

        if _state.backend_config_error_hit:
            log("Agent runner stopped — authentication failed (see message above). "
                "Re-authenticate the configured CLI backend, then run this command again.")
            sys.exit(3)
        # Honoured here rather than inside the retry branches below so a pending
        # usage-limit/overload wait — which can be hours — is skipped rather than sat
        # through. Deliberately NOT ahead of the failure path: a session that genuinely
        # failed never sets graceful_stop_hit (only the poll loop does, and only from a
        # clean seam), so a real failure still reports itself and exits 1.
        if _state.graceful_stop_hit or (
            graceful_stop_requested("implement")
            and (_state.usage_limit_hit or _state.server_overloaded_hit)
        ):
            clear_graceful_stop("implement")
            log("Agent runner stopped at your request — the session in progress was "
                "allowed to finish first, so nothing it had already done was thrown away. "
                "Run `tempa implement` (or Continue Implementation) to pick up from here.")
            sys.exit(0)
        if _state.usage_limit_hit:
            # Not a real stop — the epic/QA in progress was left exactly as check_and_run
            # would resume it (on_progress / qa_status=ongoing), so waiting out the limit
            # and re-entering the poll loop continues it rather than starting over.
            usage_limit_retries += 1
            wait_out_usage_limit("Implementation", usage_limit_retries)
            continue
        if _state.server_overloaded_hit:
            # Not a real stop either — same reasoning as the usage-limit branch above: the
            # epic/QA in progress was left resumable, so waiting out the overload and
            # re-entering the poll loop continues it rather than starting over. The reset
            # after the wait is what makes "resumable" actually hold when the overload did
            # leave a `failed` status behind — see _reset_failed_before_retry.
            overload_retries += 1
            wait_out_server_overload("Implementation", overload_retries)
            _reset_failed_before_retry("Implementation")
            continue
        if _state.backend_stuck_after_done_hit:
            # Not a real failure either: the backend CLI had already reached "[Done]" — its
            # own signal that the turn (and whatever config.json updates it makes) is
            # complete — before the process itself got stuck purely in unrelated cleanup and
            # had to be force-terminated (see the more specific message already logged by
            # _terminate_if_stuck_after_done, which names what it was doing). The epic/QA
            # state on disk is exactly what that session left it as, so resuming immediately
            # continues normally rather than starting over.
            log("Agent runner: resuming automatically after force-terminating a backend "
                "process stuck in its own post-turn cleanup (see the message above).")
            _state.backend_stuck_after_done_hit = False
            _state.stop_event.clear()
            continue
        if _state.all_done:
            log("All epics done. Agent runner stopped successfully.")
            sys.exit(0)
        log("Agent runner stopped due to session failure.")
        sys.exit(1)


def _plan_epics_run(config: dict) -> bool:
    """Study the PRD → lay out new epics/features/tasks (only what's not yet implemented) →
    write .md files to specs/pbi/epics + append to config.json, then review & fix.

    Called from within implement (not a separate command) — see main(). Returns True if
    generate + review succeed; False on a real failure or a backend-configuration error
    (check _state.backend_config_error_hit) — a usage-limit stop during either step is waited out and
    retried in place (run_with_usage_limit_retry), so it never causes this to return
    False."""
    sources = get_sources(config)
    epics_path = sources.get("epics", "")
    if not epics_path:
        log("ERROR: sources.epics not found in config.json")
        return False

    Path(epics_path).mkdir(parents=True, exist_ok=True)

    _banner(f"Plan-Epics started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  PRD={sources.get('prd', '?')} | docs={sources.get('docs', '?')} | "
          f"apps={sources.get('apps', '?')} | out={epics_path}", flush=True)

    # 1) Generate
    log("Laying out new epics/features/tasks from the PRD (only what's not yet implemented)...")
    gen_prompt = build_plan_epics_prompt(config)
    backend = get_backend_def(get_backend(config, "plan"))
    if not run_with_usage_limit_retry(
        lambda: _run_oneshot_session(
            gen_prompt, "PLAN-EPICS", "plan_epics_generate", backend,
            get_model(config, "plan"), get_reasoning_effort(config, "plan"),
        ),
        "Plan (generate epics)",
    ):
        if not _state.backend_config_error_hit:
            log("Generate epic failed — stopping.")
        return False

    # 2) Review & fix
    log("Reviewing & fixing the result (coverage, feature size < 300K, testability, parallelism)...")
    config = load_config()
    backend = get_backend_def(get_backend(config, "plan"))
    review_prompt = build_review_epics_prompt(config)
    if not run_with_usage_limit_retry(
        lambda: _run_oneshot_session(
            review_prompt, "REVIEW-EPICS", "plan_epics_review", backend,
            get_model(config, "plan"), get_reasoning_effort(config, "plan"),
        ),
        "Plan (review epics)",
    ):
        if not _state.backend_config_error_hit:
            log("Review epic failed — stopping.")
        return False

    log(f"Plan done. New epic file(s) at: {epics_path}; new epic entries have been added to config.json.")
    return True
