"""The concrete session runners: what each stage actually asks a backend to do.

`tempa_session` next door is the generic engine — spawn a CLI, stream and parse its output,
recognize the stop conditions. This module is the layer above it: one runner per stage
(implementation, QA, one-shot plan/review, clarification, apply-clarification), each
deciding what to log, what to record in config.json, and which session id to keep so the
next round can resume instead of re-reading everything.

Callers pass a fully-built prompt string in (see tempa_prompts) — nothing here builds
prompts, only runs them. What a finished implementation session MEANS for its epic is one
step further out again, in tempa_session_outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from tempa_backend import Backend, get_backend_def
from tempa_config import (
    get_backend,
    get_commit_after_qa_pass,
    get_max_qa_fail_rounds,
    get_model,
    get_qa_dir,
    get_qa_loop_strikes,
    get_reasoning_effort,
    get_workspace,
    load_config,
    save_config,
    set_epic_session_id,
)
from tempa_git import commit_workspace_changes
from tempa_logging import _print_log_tail, _state, log
from tempa_maintenance import reconcile_qa_passed_features_and_log, with_retry_hint
from tempa_notifications import AttentionEventType, notify_attention
from tempa_qa_history import VERDICT_FAIL, VERDICT_PASS, detect_qa_loop, record_qa_round
from tempa_session import _run_backend_session, _session_feature_lines
from tempa_session_outcome import apply_session_outcome

# Fields on an epic entry that the runner alone maintains — its own record of how many rounds
# this epic has had and why it was stopped. config.json is a shared surface (the spawned agent is
# told to edit its own bookkeeping there: feature statuses, completed_features, qa_status,
# qa_passed, blocked_by_epic), and nothing stops an agent from writing these too. They are never
# mentioned in any prompt except to forbid them, yet agents have written them anyway: an
# interrupted QA session once appended its own "qa_history" entry — with an invented timestamp —
# for the round it was still working on, so the runner's own record of that round landed as a
# second, identical entry. Two rounds with the same failing set is exactly the fingerprint
# detect_qa_loop reads as an epic going in circles, so a well-meaning agent edit can halt a run
# that was converging. Snapshotted before each session and put back after; see
# _snapshot_runner_owned / _restore_runner_owned.
#
# Deliberately NOT here: "blocked_by_epic" (build_session_prompt asks the agent to set it) and
# "response_message" (part of the epic skeleton plan_epics writes).
_RUNNER_OWNED_EPIC_KEYS = (
    "qa_history", "qa_loop_strikes", "blocked_reason", "total_run", "qa_total_run",
    "qa_completed_features",
)

_MISSING = object()


def _snapshot_runner_owned(index: int) -> dict:
    """The runner-owned fields of epic `index` as they stand before a session starts.

    A key that isn't set yet is left out of the snapshot, which is what tells `_restore_runner_owned`
    to delete it again rather than restore a value that never existed."""
    try:
        epic = load_config()["epic"][index]
    except (KeyError, IndexError, TypeError):
        return {}
    return {key: deepcopy(epic[key]) for key in _RUNNER_OWNED_EPIC_KEYS if key in epic}


def _restore_runner_owned(config: dict, index: int, snapshot: dict, label: str) -> bool:
    """Put `snapshot` back onto epic `index`, undoing anything the finished session wrote to a
    runner-owned field. Returns True if it had to change something (the caller saves).

    Runs before the runner writes this session's own outcome, so a legitimate post-session write
    (apply_session_outcome's blocked_reason, record_qa_round's new entry) still lands on top."""
    try:
        epic = config["epic"][index]
    except (KeyError, IndexError, TypeError):
        return False
    reverted = []
    for key in _RUNNER_OWNED_EPIC_KEYS:
        before = snapshot.get(key, _MISSING)
        if before is _MISSING:
            if key in epic:
                del epic[key]
                reverted.append(key)
        elif epic.get(key) != before:
            epic[key] = deepcopy(before)
            reverted.append(key)
    if reverted:
        log(f"[{label}] the session edited runner-owned field(s) in config.json "
            f"({', '.join(reverted)}) — restored the runner's own values. These track how many "
            "rounds this epic has had; an agent-written entry there can look like the epic "
            "cycling through QA and stop the run.")
    return bool(reverted)


def _log_session_result(label: str, exit_code: int, log_path: Path, usage_limit_note: str = "") -> bool:
    """Log SUCCEEDED / usage-limit-stopped / overload-paused / cut-short / FAILED (with a
    one-time log tail) for a finished session. Returns True iff exit_code == 0 and the
    session ran to completion — no usage limit, server overload, or backend-side
    termination of the session's own background work."""
    if _state.auth_error_hit:
        log(f"{label} stopped — authentication failed (see message above).")
        return False
    if _state.usage_limit_hit:
        log(f"{label} stopped — usage limit reached.{usage_limit_note}")
        return False
    if _state.server_overloaded_hit:
        log(f"{label} paused — backend API overloaded (will retry automatically).")
        return False
    if _state.backend_stuck_after_done_hit:
        # The non-zero exit code here is Tempa's own doing (_terminate_if_stuck_after_done
        # killed the process), not a task failure — reporting it as "FAILED (exit code 1)"
        # with a log tail made a routine, self-healing cleanup hang look alarming in the
        # dashboard's Log tab.
        log(f"{label} stopped — the backend process was force-terminated after it had already "
            "finished its turn (see the message above); resuming automatically.")
        return False
    if _state.background_tasks_terminated_hit:
        # Exit code 0 here is the CLI reporting its own clean shutdown, not a finished job:
        # it gave up waiting on the background work the session left running and killed it
        # (see the message _handle_background_terminated already logged). Reporting that as
        # "SUCCEEDED" is what made a session that was cut short mid-implementation look, in
        # the dashboard's Log tab, like one that simply had nothing left to do.
        log(f"{label} was cut short — the backend gave up waiting on the background work "
            "this session left running and terminated it (see the message above).")
        return False
    if exit_code == 0:
        log(f"{label} SUCCEEDED (exit code {exit_code})")
        return True
    log(f"{label} FAILED (exit code {exit_code})")
    _print_log_tail(log_path)
    return False


def _capture_session_id(
    index: int, backend: Backend, kind: str, initial: str | None, label: str,
) -> tuple[Callable[[dict], None], Callable[[], str | None]]:
    """Build an on_json_event callback that captures the session id from the first event
    `backend.extract_session_id` recognizes (unless `initial` is already set, e.g.
    resuming) and persists it — along with which backend produced it — to
    config["epic"][index] under the process lock (see tempa_config.set_epic_session_id).
    Returns (callback, getter)."""
    captured = [initial]

    def _on_json_event(data: dict) -> None:
        if captured[0] is not None:
            return
        sid = backend.extract_session_id(data)
        if not sid:
            return
        captured[0] = sid
        with _state.lock:
            cfg = load_config()
            set_epic_session_id(cfg["epic"][index], backend.name, sid, kind=kind)
            save_config(cfg)
        log(f"{label} session_id: {sid}", to_console=False)

    return _on_json_event, lambda: captured[0]


def run_session(
    index: int,
    prompt: str,
    session_label: str,
    resume_session_id: str | None = None,
    features_override: int | None = None,
) -> None:

    action = "Resuming" if resume_session_id else "Starting"
    backend = get_backend_def(get_backend(load_config(), "implement"))
    completed_before = load_config()["epic"][index].get("completed_features", 0)

    def _print_feature_plan() -> None:
        for _line in _session_feature_lines(load_config(), session_label, features_override):
            print(_line, flush=True)

    def _feature_progress_suffix() -> str:
        # Live feature progress: read from config.json (the agent updates completed_features
        # and each feature's status as it works). Ignore read errors (e.g. config is being
        # written) — display without feature info for that iteration. Features are worked
        # in array order, so the first non-done one is the one currently in progress.
        try:
            cfg = load_config()
            epic = next((s for s in (cfg.get("epic") or []) if s.get("epic_name") == session_label), None)
            if epic:
                completed = epic.get('completed_features', 0)
                total = epic.get('total_features', 0)
                current = next(
                    (f for f in epic.get("features", []) if f.get("status") in ("pending", "require_fixing")),
                    None,
                )
                current_part = f" — {current.get('id', '?')}" if current else ""
                return f" [feat {completed}/{total}{current_part}]"
        except Exception:
            pass
        return ""

    on_json_event, _ = _capture_session_id(index, backend, "implement", resume_session_id, f"Session [{session_label}]")
    runner_owned = _snapshot_runner_owned(index)

    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        get_model(load_config(), "implement"),
        log_prefix=f"session_{session_label}",
        banner_label=f"{action} session [{session_label}]",
        resume_session_id=resume_session_id,
        reasoning_effort=get_reasoning_effort(load_config(), "implement"),
        on_json_event=on_json_event,
        extra_progress_fn=_feature_progress_suffix,
        pre_banner_extra=_print_feature_plan,
    )

    _log_session_result(
        f"Session [{session_label}]", exit_code, log_path,
        usage_limit_note=" (epic left as on_progress so it can be resumed once the limit resets).",
    )

    with _state.lock:
        config = load_config()
        if _restore_runner_owned(config, index, runner_owned, session_label):
            save_config(config)
        apply_session_outcome(
            index, session_label, exit_code, log_path, completed_before, backend,
        )
        _state.running_thread = None
        _state.running_index = None


def _record_qa_verdict_and_guard(config: dict, index: int, session_label: str, log_path: Path) -> None:
    """Append this QA session's verdict to the epic's `qa_history` and stop the run if that
    history shows the epic cycling through the QA gate instead of converging (see
    tempa_qa_history.detect_qa_loop). Called from run_qa_session under `_state.lock`; saves
    `config` itself whenever it changed anything.

    Nothing is recorded unless the session actually reached a verdict. A QA session cut short —
    by a usage limit, an auth error, a backend overload, a stuck-after-done force-terminate, the
    backend terminating the background work the session left running, or a plain crash — leaves
    `qa_status` as "ongoing", and check_and_run resumes it as a continuation of the SAME round;
    recording on those would count one round several times and manufacture a cycle out of an
    interrupted network connection."""
    epic = config["epic"][index]
    if epic.get("qa_status") != "done":
        return
    if (_state.usage_limit_hit or _state.auth_error_hit
            or _state.server_overloaded_hit or _state.backend_stuck_after_done_hit
            or _state.background_tasks_terminated_hit):
        return

    passed = bool(epic.get("qa_passed"))
    failed_ids = [
        feature.get("id", "?") for feature in (epic.get("features") or [])
        if feature.get("status") == "require_fixing"
    ]
    record_qa_round(
        epic,
        VERDICT_PASS if passed else VERDICT_FAIL,
        failed_ids=[] if passed else failed_ids,
        report=epic.get("qa_report_filename", ""),
    )

    reason = detect_qa_loop(epic, get_qa_loop_strikes(config), get_max_qa_fail_rounds(config))
    if reason is None:
        save_config(config)
        return

    epic["status"] = "failed"
    epic["blocked_reason"] = with_retry_hint(reason)
    save_config(config)
    log(f"QA [{session_label}] — {reason}")
    notify_attention(
        AttentionEventType.QA_OSCILLATION_DETECTED,
        "QA",
        f"{session_label} keeps failing QA in circles",
        "This epic keeps failing QA on features it had already fixed. The reason below says what "
        "to compare in its QA reports to tell a real conflict from an ordinary regression, and "
        "how to get it moving again.",
        epic=session_label,
        log_path=log_path,
        details={"reason": reason, "qa_fail_rounds": sum(
            1 for entry in (epic.get("qa_history") or []) if entry.get("verdict") == VERDICT_FAIL
        )},
    )
    _state.stop_event.set()


def _stamp_qa_completed_features(config: dict, index: int) -> None:
    """Record how many features this epic had completed when the QA round that just finished
    formed its verdict, so a later implementation session can be told when its report has since
    gone out of date (see `_qa_report_staleness_note` in tempa_prompts).

    The QA prompt recalculates `completed_features` from its own verdict — the features it left
    `done` — so reading it here, right after the verdict landed, is exactly "what the report
    describes". Anything the epic completes afterwards is work that report never saw.

    Only stamped once the round actually reached a verdict, for the same reason
    `_record_qa_verdict_and_guard` above only records then: a session cut short leaves
    `qa_status` "ongoing" and gets resumed as the same round, and stamping mid-round would date
    the report to a verdict it hadn't reached yet."""
    epic = config["epic"][index]
    if epic.get("qa_status") != "done":
        return
    stamped = epic.get("completed_features", 0)
    if epic.get("qa_completed_features") == stamped:
        return
    epic["qa_completed_features"] = stamped
    save_config(config)


def run_qa_session(
    index: int,
    prompt: str,
    session_label: str,
    resume_session_id: str | None = None,
) -> None:

    get_qa_dir().mkdir(parents=True, exist_ok=True)
    action = "Resuming" if resume_session_id else "Starting"
    backend = get_backend_def(get_backend(load_config(), "implement"))

    on_json_event, _ = _capture_session_id(index, backend, "qa", resume_session_id, f"QA [{session_label}]")
    runner_owned = _snapshot_runner_owned(index)

    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        get_model(load_config(), "implement"),
        log_prefix=f"qa_{session_label}",
        banner_label=f"{action} QA session [{session_label}]",
        resume_session_id=resume_session_id,
        reasoning_effort=get_reasoning_effort(load_config(), "implement"),
        progress_tag="QA",
        on_json_event=on_json_event,
    )

    _log_session_result(f"QA session [{session_label}]", exit_code, log_path)

    # qa_status is managed by the agent in config.json.
    # If it is still "ongoing" after this session, check_and_run will detect and resume.
    with _state.lock:
        # A pass verdict leaves the feature statuses of an earlier failed QA round untouched
        # (the PASS branch of the QA prompt only writes qa_passed/qa_status) — resync them
        # here, right after the verdict lands, so the status output and the dashboard never
        # show this epic as QA-passed with 🔧 features. check_and_run does the same on every
        # poll as the catch-all; this call is what makes the repair immediate.
        config = load_config()
        # Undo any runner-owned bookkeeping the QA agent wrote BEFORE the round it just finished
        # is recorded — an agent-written qa_history entry for this same round would otherwise sit
        # in the history that _record_qa_verdict_and_guard is about to append to and judge.
        repaired = _restore_runner_owned(config, index, runner_owned, session_label)
        if reconcile_qa_passed_features_and_log(config) or repaired:
            save_config(config)

        # Record this round's verdict and check the epic's QA history for a loop before anything
        # else reads its status — a trip rewrites it to "failed".
        _record_qa_verdict_and_guard(config, index, session_label, log_path)

        epic = config["epic"][index]
        _stamp_qa_completed_features(config, index)
        if (epic.get("status") == "done" and epic.get("qa_passed")
                and epic.get("qa_status") == "done" and get_commit_after_qa_pass(config)):
            workspace_root = get_workspace(config).get("root", "")
            label = epic.get("epic_name", session_label)
            outcome, detail = commit_workspace_changes(
                workspace_root, f"tempa: {label} — QA passed"
            )
            if outcome == "committed":
                log(f"[{label}] committed workspace changes after QA pass: {detail}")
            else:
                log(f"[{label}] commit after QA pass {outcome}: {detail}")

        _state.running_thread = None
        _state.running_index = None


def _run_oneshot_session(
    prompt: str, label: str, log_prefix: str, backend: Backend, model: str, reasoning_effort: str = "",
) -> bool:
    """Run a single fresh session (never resumes) against `backend`. Streams output to a
    log file and returns True on exit code 0. Used by one-pass workflows (plan-epics,
    review)."""
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=log_prefix,
        banner_label=label,
        reasoning_effort=reasoning_effort,
        progress_tag=label,
    )
    return _log_session_result(f"[{label}]", exit_code, log_path)


def _capture_clarify_session_id(
    backend: Backend, initial: str | None, label: str, id_key: str = "clarify_session_id",
    backend_key: str = "clarify_session_backend",
) -> Callable[[dict], None]:
    """Like _capture_session_id, but for clarify/apply sessions — these aren't tied to an
    epic index, so the captured id is persisted directly under top-level config.json keys
    instead of into an epic entry. `id_key`/`backend_key` default to the evaluate session's
    keys (see tempa_config.get_clarify_session_id); run_apply_clarification_session passes
    the apply-specific pair instead (get_clarify_apply_session_id)."""
    captured = [initial]

    def _on_json_event(data: dict) -> None:
        if captured[0] is not None:
            return
        sid = backend.extract_session_id(data)
        if not sid:
            return
        captured[0] = sid
        with _state.lock:
            cfg = load_config()
            cfg[id_key] = sid
            cfg[backend_key] = backend.name
            save_config(cfg)
        log(f"{label} session_id: {sid}", to_console=False)

    return _on_json_event


def run_clarification_session(
    prompt: str, run_number: int, backend: Backend, model: str, reasoning_effort: str = "",
) -> bool:
    """Run a single clarification (evaluate) session against `backend`. Always starts a
    fresh session — never resumes itself (a fresh full read of the PRD every round is
    what makes evaluate trustworthy). Its session id IS captured (into
    config["clarify_session_id"]), purely so a same-backend apply pass run right after it
    (run_apply_clarification_session) can resume it — that session already paid to read
    the whole PRD, so applying via --resume reuses that context instead of re-reading it
    cold."""
    label = f"Clarification run #{run_number}"
    on_json_event = _capture_clarify_session_id(backend, None, label)
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=f"clarification_{run_number}",
        banner_label=label,
        reasoning_effort=reasoning_effort,
        progress_tag="CLARIFY",
        on_json_event=on_json_event,
    )
    return _log_session_result(label, exit_code, log_path)


def run_apply_clarification_session(
    prompt: str, run_number: int, backend: Backend, model: str, reasoning_effort: str = "",
    resume_session_id: str | None = None,
) -> bool:
    """Apply clarification findings to PRD/spec documents against `backend`.

    `resume_session_id`, when given, resumes an existing session instead of starting
    fresh — normally the evaluate session that just wrote the findings being applied
    (see tempa_config.get_clarify_session_id / _run_apply_step), so the apply pass reuses
    context that session already paid to build instead of re-reading the PRD and every
    backlog clarification file cold. Omit it (e.g. a standalone `tempa clarify --apply`
    run some time after evaluate, or a backend mismatch) to fall back to a fresh session,
    same as before."""
    label = f"Apply-clarifications run #{run_number}"
    action = "Resuming" if resume_session_id else "Starting"
    # Captured under its own top-level keys (distinct from the evaluate session's
    # clarify_session_id) so a usage-limit/overload retry of THIS apply attempt (see
    # tempa_config.get_clarify_apply_session_id) can resume it instead of losing whatever
    # this attempt already did and falling back to resuming evaluate — or starting cold
    # — again.
    on_json_event = _capture_clarify_session_id(
        backend, resume_session_id, label,
        id_key="clarify_apply_session_id", backend_key="clarify_apply_session_backend",
    )
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=f"apply_clarification_{run_number}",
        banner_label=f"{action} {label}",
        resume_session_id=resume_session_id,
        reasoning_effort=reasoning_effort,
        progress_tag="APPLY",
        on_json_event=on_json_event,
    )
    return _log_session_result(label, exit_code, log_path)
