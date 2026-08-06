"""The implement pipeline: the poll loop, the top-level `main` entry, and plan-epics.

`main` decides whether to run plan first (no pending work, or --replan) then loops calling
`check_and_run` every POLL_INTERVAL_SEC seconds. `check_and_run` is the scheduler: it picks
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
    POLL_INTERVAL_SEC,
    WORKING_DIR,
    get_backend,
    get_config_path,
    get_epic_session_id,
    get_model,
    get_qa_dir,
    get_reasoning_effort,
    get_sources,
    load_config,
    save_config,
)
from tempa_logging import _banner, _init_process_log, _state, log, process_log_path
from tempa_maintenance import reset_failed_epics
from tempa_prompts import (
    build_plan_epics_prompt,
    build_qa_prompt,
    build_review_epics_prompt,
    build_session_prompt,
)
from tempa_session import (
    _run_oneshot_session,
    run_qa_session,
    run_session,
    run_with_usage_limit_retry,
    wait_out_server_overload,
    wait_out_usage_limit,
)


def _validate_and_increment_run(config: dict, index: int, label: str) -> bool:
    """Increment total_run and validate against max_session_run. Returns False if limit exceeded."""
    max_run = config.get("max_session_run")
    total_run = config["epic"][index].get("total_run", 0)
    if max_run is not None and total_run >= max_run:
        log(f"Session [{label}] has reached the max_session_run limit ({max_run}). Stopping.")
        return False
    config["epic"][index]["total_run"] = total_run + 1
    return True


def _validate_and_increment_qa_run(config: dict, index: int, label: str) -> bool:
    """Increment qa_total_run and validate against max_session_run. Returns False if limit exceeded."""
    max_run = config.get("max_session_run")
    qa_total_run = config["epic"][index].get("qa_total_run", 0)
    if max_run is not None and qa_total_run >= max_run:
        log(f"QA [{label}] has reached the max_session_run limit ({max_run}). Skipping QA.")
        config["epic"][index]["qa_passed"] = True
        config["epic"][index]["qa_status"] = "done"
        return False
    config["epic"][index]["qa_total_run"] = qa_total_run + 1
    return True


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


def check_and_run(features_override: int | None = None) -> None:

    with _state.lock:
        if _state.running_thread is not None and _state.running_thread.is_alive():
            log("Session in progress — skipping poll", to_console=False)
            return

        config = load_config()

        # QA resumption: if any epic has qa_status="ongoing", resume that QA session first
        for i, session in enumerate(config["epic"]):
            if session.get("qa_status") == "ongoing":
                label = session.get("epic_name", f"epic_{i}")
                resume_sid = get_epic_session_id(session, get_backend(config, "implement"), kind="qa")
                log(f"QA [{label}] was interrupted (qa_status=ongoing) — resuming with session_id: {resume_sid}")

                if not _validate_and_increment_qa_run(config, i, label):
                    save_config(config)
                    return

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
                return

        # Handle stale on_progress session — always start a new session
        for i, session in enumerate(config["epic"]):
            if session["status"] == "on_progress":
                label = session.get("epic_name", f"epic_{i}")

                total = session.get("total_features", 0)
                completed = session.get("completed_features", 0)
                progress_str = f"{completed}/{total}" if total else "?"
                log(f"Session [{label}] progress: {progress_str} features — starting a new session")

                prompt = build_session_prompt(
                    config, session.get("epic_name", ""),
                    is_continuation=completed > 0, features_override=features_override,
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
                    args=(i, prompt, label),
                    kwargs={"features_override": features_override},
                    daemon=True,
                )
                _state.running_thread.start()
                return

        # QA gate: check for any "done" epic that has not yet passed QA (one at a time, in order)
        for i, session in enumerate(config["epic"]):
            if session["status"] == "done" and not session.get("qa_passed", False):
                label = session.get("epic_name", f"epic_{i}")

                # Block if any PREVIOUS epic's QA found issues and is waiting for re-implementation.
                # qa_status="done" + qa_passed=false means QA ran and failed — that epic must be
                # re-implemented (and re-QA'd) before we advance to this one.
                blocked_by = next(
                    (config["epic"][j].get("epic_name", f"epic_{j}")
                     for j in range(i)
                     if not config["epic"][j].get("qa_passed", False)
                     and config["epic"][j].get("qa_status") not in ("idle", None)),
                    None,
                )
                if blocked_by:
                    log(f"QA [{label}] deferred — waiting for [{blocked_by}] re-implementation + QA to finish first")
                    return

                log(f"QA is required for [{label}] before continuing implementation")

                if not _validate_and_increment_qa_run(config, i, label):
                    save_config(config)
                    return

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
                return

        # Find first require_fixing epic (prioritized — already implemented but needs QA fixes)
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
            log("All epics done — agent runner stopping.")
            _state.stop_event.set()
            return

        # All epics before next_index must not be failed
        for i in range(next_index):
            if config["epic"][i]["status"] == "failed":
                label = config["epic"][i].get("epic_name", f"epic_{i}")
                log(f"Halted — session [{label}] at index {i} has failed. Fix it, then run "
                    "`tempa implement --reset-failed` (failed → pending) before proceeding.")
                raise SystemExit(1)

        session = config["epic"][next_index]
        label = session.get("epic_name", f"epic_{next_index}")
        is_require_fixing = session["status"] == "require_fixing"
        is_continuation = is_require_fixing or session.get("completed_features", 0) > 0
        prompt = build_session_prompt(
            config, session.get("epic_name", ""),
            is_continuation=is_continuation,
            features_override=features_override,
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
            args=(next_index, prompt, label),
            kwargs={"features_override": features_override},
            daemon=True,
        )
        _state.running_thread.start()


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

    qa_report_filename = target.get("qa_report_filename", "")
    if qa_report_filename and Path(qa_report_filename).exists():
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
    return False


def main(features_override: int | None = None, replan: bool = False) -> None:
    _init_process_log()

    config = load_config()
    if replan or not _has_pending_work(config):
        if replan:
            log("--replan given — running plan (lay out epic/feature/task) before implementation.")
        else:
            log("No task (epic/feature/QA) to work on — running plan automatically "
                "before implementation.")
        if not _plan_epics_run(config):
            if _state.auth_error_hit:
                sys.exit(3)
            log("Plan failed — agent runner stopping.")
            sys.exit(1)

    start_time = datetime.now()
    banner_parts = [
        f"Agent Runner started {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"dir={WORKING_DIR}",
        f"poll={POLL_INTERVAL_SEC}s",
    ]
    _plog = process_log_path()
    if _plog:
        banner_parts.append(f"log={_plog.name}")
    if features_override is not None:
        banner_parts.append(f"features/session={features_override}")
    _banner(" | ".join(banner_parts))

    _print_session_plan(load_config(), features_override)

    log(f"Agent runner started — working dir: {WORKING_DIR}", to_console=False)
    log(f"Poll interval: {POLL_INTERVAL_SEC}s | Config: {get_config_path()}", to_console=False)
    if features_override is not None:
        log(f"features_per_session override: {features_override}", to_console=False)

    usage_limit_retries = 0
    overload_retries = 0
    while True:
        while not _state.stop_event.is_set():
            try:
                check_and_run(features_override=features_override)
            except SystemExit:
                raise
            except Exception as e:
                log(f"Unexpected error in check_and_run: {e}")

            _state.stop_event.wait(timeout=POLL_INTERVAL_SEC)

        if _state.auth_error_hit:
            log("Agent runner stopped — authentication failed (see message above). "
                "Re-authenticate the configured CLI backend, then run this command again.")
            sys.exit(3)
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
        if _state.all_done:
            log("All epics done. Agent runner stopped successfully.")
            sys.exit(0)
        log("Agent runner stopped due to session failure.")
        sys.exit(1)


def _plan_epics_run(config: dict) -> bool:
    """Study the PRD → lay out new epics/features/tasks (only what's not yet implemented) →
    write .md files to specs/pbi/epics + append to config.json, then review & fix.

    Called from within implement (not a separate command) — see main(). Returns True if
    generate + review succeed; False on a real failure or an auth error (check
    _state.auth_error_hit) — a usage-limit stop during either step is waited out and
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
        if not _state.auth_error_hit:
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
        if not _state.auth_error_hit:
            log("Review epic failed — stopping.")
        return False

    log(f"Plan done. New epic file(s) at: {epics_path}; new epic entries have been added to config.json.")
    return True
