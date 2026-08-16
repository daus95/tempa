"""The agent-runner session engine, shared by every backend (Claude Code, GitHub Copilot
CLI, OpenAI Codex CLI).

Everything involved in spawning a backend's CLI, feeding it a prompt, streaming and parsing
its output to a log file, showing live progress, and detecting the two "stop everything"
failure modes (usage-limit and authentication errors) lives here — generically, driven by
the active `tempa_backend.Backend`. Also the concrete session runners built on top of that
core: implementation, QA, one-shot (plan/review), clarification, and apply-clarification.
The usage-limit pause/retry helpers (`wait_out_usage_limit`, `run_with_usage_limit_retry`)
live here too, since every caller that hits a usage-limit stop needs them.

Callers pass a fully-built prompt string in (see tempa_prompts) — this module never builds
prompts, only runs them.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from tempa_backend import AUTONOMOUS_SYSTEM_PROMPT, Backend, get_backend_def, resolve_exe
from tempa_config import (
    WORKING_DIR,
    get_backend,
    get_backend_background_wait_sec,
    get_commit_after_qa_pass,
    get_logs_dir,
    get_max_qa_fail_rounds,
    get_model,
    get_qa_dir,
    get_qa_loop_strikes,
    get_reasoning_effort,
    get_server_overloaded_retry_wait_sec,
    get_usage_limit_heartbeat_sec,
    get_usage_limit_retry_wait_sec,
    get_workspace,
    load_config,
    save_config,
    set_epic_session_id,
)
from tempa_git import commit_workspace_changes
from tempa_logging import SHOW_PROMPT, _banner, _print_log_tail, _state, log
from tempa_maintenance import reconcile_qa_passed_features_and_log
from tempa_notifications import AttentionEventType, notify_attention
from tempa_qa_history import (
    VERDICT_FAIL,
    VERDICT_PASS,
    detect_qa_loop,
    record_qa_round,
)
from tempa_session_outcome import apply_session_outcome


def _is_usage_limit_text(text: str, backend: Backend) -> bool:
    """True if the given CLI output text indicates `backend` hit a usage limit."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in backend.usage_limit_markers)


def _handle_usage_limit(text: str, process: subprocess.Popen, label: str, backend: Backend) -> bool:
    """If text indicates a usage-limit failure, flag a global stop, terminate the
    running process, and return True. Otherwise return False."""
    if not _is_usage_limit_text(text, backend):
        return False
    _state.usage_limit_hit = True
    log(f"[{label}] {backend.label} usage limit reached — stopping the agent runner.")
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


def _is_overloaded_text(text: str, backend: Backend) -> bool:
    """True if the given CLI output text indicates `backend`'s API reported itself
    overloaded (a transient, server-side condition — e.g. Anthropic's 529 status) rather
    than a usage limit or a real failure."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in backend.overloaded_markers)


def _handle_overloaded(text: str, process: subprocess.Popen, label: str, backend: Backend) -> bool:
    """If text indicates the backend's API is overloaded, flag a global stop, terminate the
    running process, and return True. Otherwise return False."""
    if not _is_overloaded_text(text, backend):
        return False
    _state.server_overloaded_hit = True
    log(f"[{label}] {backend.label} reported its API is overloaded — pausing the agent "
        "runner (this is a transient, server-side issue, not a real failure).")
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


def _is_background_terminated_text(text: str, backend: Backend) -> bool:
    """True if the given CLI output text is `backend` announcing it has killed the
    background work the current turn left running because its own wait ceiling expired
    (see Backend.background_terminated_markers)."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in backend.background_terminated_markers)


def _handle_background_terminated(text: str, label: str, backend: Backend) -> bool:
    """If text shows the backend killed its own still-running background work, flag it and
    return True. Otherwise return False.

    Deliberately does NOT terminate the process or set stop_event, unlike every other
    handler here: the CLI is already tearing itself down and exits 0 straight after, and
    the session's work up to that point is real and on disk. All this flag does is stop
    run_session from reading that truncated session as an epic that made no progress
    because it's blocked — it was cut short mid-flight, so the right answer is to resume
    it, not to fail it."""
    if not _is_background_terminated_text(text, backend):
        return False
    _state.background_tasks_terminated_hit = True
    log(f"[{label}] {backend.label} hit its own ceiling on waiting for background work "
        "(a delegated sub-agent, or a command left running in the background) and killed "
        "it, cutting this session short. Whatever it finished before that is on disk and "
        "the session stays resumable, so this round is not counted as a stalled one. "
        "Raise `backend_background_wait_sec` in config.json if this keeps happening.")
    return True


def _is_auth_error_text(text: str, backend: Backend) -> bool:
    """True if the given CLI output text indicates an authentication/credential failure
    (expired login, bad API key) rather than a usage limit or a generic bug."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in backend.auth_error_markers)


def _handle_auth_error(text: str, process: subprocess.Popen, label: str, backend: Backend) -> bool:
    """If text indicates an authentication failure, flag a global stop (every subsequent
    session would fail the same way until the user re-authenticates), terminate the
    running process, and return True. Otherwise return False."""
    if not _is_auth_error_text(text, backend):
        return False
    _state.auth_error_hit = True
    _state.auth_error_message = backend.friendly_auth_error_message(text)
    log(f"[{label}] {_state.auth_error_message}")
    notify_attention(
        AttentionEventType.AUTHENTICATION_REQUIRED,
        label,
        f"{backend.label} authentication failed",
        "Re-authenticate the configured CLI backend, then run the command again.",
        details={"backend": backend.name},
    )
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


def _failure_marker_text(raw_line: str, data: dict | None) -> str:
    """Return text eligible for backend-failure marker matching.

    Backend JSON events can embed arbitrary command output.  Matching markers against the
    whole serialized event therefore turns application text such as an OpenAPI
    ``#/components/responses/Unauthorized`` reference into a false CLI authentication
    failure.  Plain stderr and structured failure events remain eligible; successful and
    informational JSON events do not.
    """
    if data is None:
        return raw_line

    event_type = str(data.get("type", "")).lower()
    if event_type == "error" or "failed" in event_type or data.get("is_error") is True:
        return raw_line

    if event_type == "result":
        exit_code = data.get("exitCode", data.get("exit_code"))
        if exit_code not in (None, 0, "0"):
            return raw_line

    return ""


# ---------------------------------------------------------------------------
# Usage-limit / server-overload pause & retry
# ---------------------------------------------------------------------------
# A usage-limit stop (see _handle_usage_limit above) or a server-overload stop (see
# _handle_overloaded above) is not a real failure — nothing is broken. For a usage limit,
# the backend CLI's subscription/session token allowance simply ran out for now; for an
# overload, the backend's own API (a system outside Tempa/the target app entirely) is
# temporarily rejecting requests. Every clarify/implement/verify entry point that can hit
# either one waits it out and then retries the exact step that was interrupted, instead of
# failing the whole command over it — see run_with_usage_limit_retry below, used throughout
# tempa_clarify.py and tempa_implement.py. Whatever state that step depends on
# (clarification files and their recorded answers, config.json's epic/feature/QA progress)
# already lives on disk, so the retried step picks up where it left off rather than
# starting over. This is also what the dashboard's background clarify/implement runs
# (dashboard_runs.py) inherit for free — they just spawn `tempa.py clarify`/`tempa.py
# implement` as a subprocess and stream its console output, so this same wait-then-continue
# happens inside that subprocess with no dashboard-side logic of its own needed.
# Authentication errors (see _handle_auth_error) are deliberately NOT retried this way —
# waiting can't fix an expired/invalid credential, only re-authenticating can.
#
# The wait/heartbeat durations themselves are configurable (config.json's
# usage_limit_retry_wait_sec / usage_limit_heartbeat_sec / server_overloaded_retry_wait_sec,
# see tempa_config.py) — read fresh via load_config() on every wait below (which never
# caches, see load_config's docstring) rather than once at import time, so a Settings
# change reaches an already-running wait on its very next check, no restart needed.


def wait_out_usage_limit(label: str, attempt: int) -> None:
    """Block for config.json's usage_limit_retry_wait_sec (emitting periodic heartbeat log
    lines every usage_limit_heartbeat_sec), then clear `_state.usage_limit_hit` and
    `_state.stop_event` so the caller can retry `label`'s just-interrupted step. `attempt`
    (1, 2, 3, ...) only affects the log wording — callers increment it themselves across
    repeated calls."""
    config = load_config()
    retry_wait_sec = get_usage_limit_retry_wait_sec(config)
    heartbeat_sec = get_usage_limit_heartbeat_sec(config)
    resume_at = datetime.now().timestamp() + retry_wait_sec
    resume_str = datetime.fromtimestamp(resume_at).strftime("%Y-%m-%d %H:%M:%S")
    log(f"{label} paused — usage limit reached on the configured backend. This is not an "
        f"error: waiting {retry_wait_sec // 60} minutes for the limit to reset, "
        f"then retrying automatically (retry #{attempt}, around {resume_str}).")
    remaining = retry_wait_sec
    while remaining > 0:
        chunk = min(heartbeat_sec, remaining)
        time.sleep(chunk)
        remaining -= chunk
        if remaining > 0:
            log(f"Still waiting for the usage limit to reset — about {remaining // 60} "
                "more minute(s)...")
    log(f"Usage-limit wait over — retrying {label} now (retry #{attempt})...")
    _state.usage_limit_hit = False
    _state.stop_event.clear()


def wait_out_server_overload(label: str, attempt: int) -> None:
    """Block for config.json's server_overloaded_retry_wait_sec, then clear
    `_state.server_overloaded_hit` and `_state.stop_event` so the caller can retry
    `label`'s just-interrupted step. `attempt` (1, 2, 3, ...) only affects the log
    wording — callers increment it themselves across repeated calls."""
    retry_wait_sec = get_server_overloaded_retry_wait_sec(load_config())
    resume_at = datetime.now().timestamp() + retry_wait_sec
    resume_str = datetime.fromtimestamp(resume_at).strftime("%Y-%m-%d %H:%M:%S")
    log(f"{label} paused — the backend's API reported it is overloaded. This is not an "
        f"error on Tempa's or the target app's side: waiting {retry_wait_sec // 60} "
        f"minutes, then retrying automatically (retry #{attempt}, around {resume_str}).")
    time.sleep(retry_wait_sec)
    log(f"Overload wait over — retrying {label} now (retry #{attempt})...")
    _state.server_overloaded_hit = False
    _state.stop_event.clear()


def run_with_usage_limit_retry(run_fn: Callable[[], bool], label: str) -> bool:
    """Call the zero-arg `run_fn` — one clarify/implement/verify session already bound to
    its arguments, returning True on success like every `run_*_session` in this module —
    and, for as long as it fails specifically because the backend's usage limit was hit or
    its API reported itself overloaded, wait it out (wait_out_usage_limit /
    wait_out_server_overload) and call it again. Returns `run_fn()`'s own result once it
    either succeeds or fails for any other reason (a real failure, or an auth error) — the
    caller still checks `_state.auth_error_hit` itself afterward, exactly as if this retry
    loop weren't here."""
    usage_limit_attempt = 0
    overload_attempt = 0
    while True:
        ok = run_fn()
        if ok:
            return ok
        if _state.usage_limit_hit:
            usage_limit_attempt += 1
            wait_out_usage_limit(label, usage_limit_attempt)
            continue
        if _state.server_overloaded_hit:
            overload_attempt += 1
            wait_out_server_overload(label, overload_attempt)
            continue
        return ok


def _session_feature_lines(config: dict, epic_label: str, features_override: int | None) -> list[str]:
    """Console lines describing feature progress for an epic: 'X/Y done' plus the batch of
    features (🔧 require_fixing / ⬜ pending) that will be processed in this session, honoring
    features_per_session. Returns [] if the epic has no entry."""
    epic = next((s for s in (config.get("epic") or []) if s.get("epic_name") == epic_label), None)
    if not epic:
        return []
    total = epic.get("total_features", len(epic.get("features", [])))
    completed = epic.get("completed_features", 0)
    todo = [f for f in epic.get("features", []) if f.get("status") in ("pending", "require_fixing")]
    fps = features_override if features_override is not None else config.get("features_per_session")
    batch = todo[:fps] if fps else todo

    lines = [f"Features done: {completed}/{total}"]
    if batch:
        lines.append(f"Queued for this session ({len(batch)} feature(s)):")
        for f in batch:
            icon = "🔧" if f.get("status") == "require_fixing" else "⬜"
            lines.append(f"  {icon} {f.get('id', '?')} — {f.get('name', '')}")
        if fps and len(todo) > fps:
            lines.append(f"  ... (+{len(todo) - fps} more feature(s) in the next session)")
    return lines


def prepare_backend_invocation(
    backend: Backend,
    model: str,
    resume_session_id: str | None,
    prompt: str,
    log_path: Path,
    reasoning_effort: str = "",
) -> tuple[list[str], str]:
    """Resolve `backend`'s executable and build (argv, stdin_text) for one invocation.

    Handles the two prompt-delivery modes (see tempa_backend.Backend.prompt_mode):
    - "stdin": the full prompt (with the autonomous-pipeline system banner prepended, for
      backends without a dedicated system-prompt flag) is returned as stdin_text for the
      caller to pipe in.
    - "file_ref": the full prompt is written to a sidecar file next to log_path, and the
      returned argv's CLI-visible instruction is a short single-line pointer at that file
      instead — the actual prompt never touches argv, avoiding the Windows .cmd-shim
      multi-line-argument truncation issue. stdin_text is "" in this case.

    `reasoning_effort` ("" = no override) is passed straight through to `backend.build_cmd` —
    the caller is responsible for having validated it against the model via
    `tempa_backend.is_valid_reasoning_effort` before getting here.

    Raises FileNotFoundError if the backend's executable isn't on PATH.
    """
    exe = resolve_exe(backend)
    if not exe:
        raise FileNotFoundError(f"{backend.label} CLI not found in PATH (tried: {', '.join(backend.exe_names)})")

    full_prompt = prompt if backend.append_system_prompt else (
        f"<system>\n{AUTONOMOUS_SYSTEM_PROMPT}\n</system>\n\n{prompt}"
    )

    if backend.prompt_mode == "file_ref":
        prompt_file = log_path.with_name(log_path.stem + ".prompt.md")
        prompt_file.write_text(full_prompt, encoding="utf-8")
        prompt_arg = (
            f"Read the file at {prompt_file} for your complete task instructions and follow "
            "them exactly. That file is your entire task for this session — do not summarize "
            "it back or ask for confirmation, just do it."
        )
        return backend.build_cmd(exe, model, resume_session_id, prompt_arg, reasoning_effort), ""

    return backend.build_cmd(exe, model, resume_session_id, None, reasoning_effort), full_prompt


# On Windows, a pipe's write handle is inheritable by default, so a grandchild the backend
# CLI spawns (a build tool, a leftover `dotnet run` server left listening for the rest of
# the session, etc.) can hold our stdout pipe open even after the CLI process itself has
# exited and finished printing its `[Done]` line. Without a bound, `for line in
# process.stdout` then blocks forever, and a session sits reported as "Running..." with a
# frozen row count. Once the process itself has exited, only wait this long for any
# already-buffered output before giving up on the pipe.
_STDOUT_DRAIN_GRACE_SEC = 3.0


def _read_process_stdout(process: subprocess.Popen, drain_grace_sec: float = _STDOUT_DRAIN_GRACE_SEC) -> Iterator[str]:
    """Yield lines from `process.stdout`, but stop waiting once `process` has exited and
    `drain_grace_sec` has passed with nothing more buffered — instead of blocking forever
    on a pipe a lingering grandchild process is still holding open (see
    `_STDOUT_DRAIN_GRACE_SEC`)."""
    line_queue: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        try:
            for raw_line in process.stdout:
                line_queue.put(raw_line)
        finally:
            line_queue.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    process_exited = False
    while True:
        try:
            raw_line = line_queue.get(timeout=drain_grace_sec if process_exited else 0.5)
        except queue.Empty:
            if process_exited:
                return  # grace period elapsed with nothing more buffered — give up on the pipe
            process_exited = process.poll() is not None
            continue
        if raw_line is None:
            return  # pipe closed normally
        yield raw_line


# How long to let a backend CLI take to actually exit after it has already signaled its
# turn is complete (a "[Done] ..." readable line, per each backend's parse_line) AND gone
# completely silent, before assuming it's stuck and force-terminating it. Seen live: codex
# tried, as its very last action, to stop a background test process it had spawned; that
# cleanup command was rejected by its own sandbox policy, and the process itself never
# exited afterward — an otherwise fully finished QA session (report written, config.json
# already updated) sat "Running..." in the dashboard for 17+ minutes with nothing left to
# actually wait for.
_POST_DONE_EXIT_GRACE_SEC = 120.0


def _apply_done_signal(readable: str, done_event: threading.Event) -> None:
    """Arm/disarm the stuck-after-done watchdog from a parsed readable output line.

    A backend's "[Done]" line marks the end of a *turn*, not necessarily the end of the
    process: Claude Code emits one `result` event per re-invocation inside a single `-p` run,
    so several "[Done]" lines with real work after them in the same session log is normal.
    Worse, a resumed session can replay a wakeup its previous session left pending as a
    "[Done] turns=0" on the second line of the log and only then start working — seen live in
    session_EPIC-05_20260815_032220.txt, where latching on that first "[Done]" got a perfectly
    healthy session force-terminated 120s later, mid-`dotnet test`.

    So arm on "[Done]", but disarm again the moment any further agent output arrives: the
    watchdog can then only ever fire on a process that has gone genuinely silent after
    signaling it was finished.

    Deliberate narrowing: a process that hangs forever without "[Done]" being its last output
    is no longer covered (session_EPIC-05_20260815_000744.txt ends on a tool line, for
    instance — it exited fine, but a hang in that shape would now go unnoticed). That's the
    right trade: the silence gate has to stay, since an agent can legitimately run a
    ten-minute test suite emitting nothing, and killing live sessions is a far worse failure
    mode than occasionally waiting on a hung one. The hang this watchdog was built for (codex
    wedged in its own post-turn cleanup) does emit "[Done]" first."""
    if readable.startswith("[Done]"):
        done_event.set()
    else:
        done_event.clear()


def _grace_period_outcome(
    process: subprocess.Popen,
    done_event: threading.Event,
    grace_sec: float,
    poll_interval: float,
    sleep_fn: Callable[[float], None],
) -> str:
    """Sleep out `grace_sec` in `poll_interval` slices, stopping early if anything makes the
    wait pointless. Returns "exited" (the process ended on its own), "resumed" (more output
    arrived, so `_apply_done_signal` cleared `done_event` — the backend wasn't finished after
    all), or "stuck" (the full grace period elapsed with the process alive and still done)."""
    waited = 0.0
    while waited < grace_sec:
        step = min(poll_interval, grace_sec - waited)
        sleep_fn(step)
        waited += step
        if process.poll() is not None:
            return "exited"
        if not done_event.is_set():
            return "resumed"
    return "stuck"


def _terminate_if_stuck_after_done(
    process: subprocess.Popen,
    done_event: threading.Event,
    label: str,
    grace_sec: float = _POST_DONE_EXIT_GRACE_SEC,
    poll_interval: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Background watchdog (run as a daemon thread alongside `_stream_backend_process`'s own
    read loop): once the backend has signaled its turn is complete (`done_event` set), give
    it `grace_sec` to exit the process on its own — a CLI can reasonably take a few seconds
    to flush/clean up — and force-terminate it if it hasn't, instead of leaving Tempa (and
    the dashboard) waiting on a process that may never exit by itself.

    Returns without doing anything if the process exits on its own at any point, whether or
    not it ever signaled `[Done]` first — there's nothing to fix in that case. And if more
    output arrives during the grace period (`done_event` cleared by `_apply_done_signal`),
    the backend is demonstrably still working: go back to waiting for the next `[Done]`
    rather than killing a live session."""
    while True:
        while process.poll() is None and not done_event.is_set():
            sleep_fn(poll_interval)
        if process.poll() is not None:
            return
        outcome = _grace_period_outcome(process, done_event, grace_sec, poll_interval, sleep_fn)
        if outcome == "exited":
            return
        if outcome == "resumed":
            continue
        log(
            f"[{label}] finished its turn {grace_sec:.0f}s ago and has produced no output since, "
            "but the backend CLI process itself never exited — likely stuck in its own post-turn "
            "cleanup (e.g. trying to stop something it spawned). Its actual output up to that "
            "point is unaffected; force-terminating the process instead of waiting on it "
            "indefinitely."
        )
        _state.backend_stuck_after_done_hit = True
        _state.stop_event.set()
        with contextlib.suppress(Exception):
            process.terminate()
        return


def _backend_env(backend: Backend) -> dict[str, str]:
    """The environment to spawn `backend`'s CLI with: this process's own environment plus
    the backend's `background_wait_env` for Tempa's configured
    `backend_background_wait_sec`.

    Those are applied as DEFAULTS, never overrides — a variable the user already exported
    themselves wins, so tuning one by hand (or pinning it in CI) keeps working exactly as
    it did before Tempa set anything."""
    env = dict(os.environ)
    for name, value in backend.background_wait_env(get_backend_background_wait_sec(load_config())).items():
        env.setdefault(name, value)
    return env


def _stream_backend_process(
    backend: Backend,
    cmd: list[str],
    stdin_text: str,
    log_path: Path,
    label: str,
    row_count: list[int],
    on_json_event: Callable[[dict], None] | None = None,
) -> int:
    """Spawn `cmd`, feed `stdin_text` via stdin (may be empty — e.g. file_ref-mode
    backends need nothing on stdin), and stream stdout to `log_path` — the shared core
    loop behind every backend-invoking runner. Parses each JSON-lines event through
    `backend.parse_line` into readable text (falls back to the raw line for anything that
    isn't valid JSON), stops early if a usage-limit/auth-error marker is seen, and invokes
    `on_json_event(data)` per parsed event for callers that need to react (e.g. capture a
    session id or a final result)."""
    _state.background_tasks_terminated_hit = False
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            cwd=str(WORKING_DIR),
            env=_backend_env(backend),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if stdin_text:
            process.stdin.write(stdin_text)
        process.stdin.close()

        done_event = threading.Event()
        threading.Thread(
            target=_terminate_if_stuck_after_done, args=(process, done_event, label), daemon=True,
        ).start()

        for raw_line in _read_process_stdout(process):
            row_count[0] += 1
            line = raw_line.strip()
            data = None
            if line.startswith("{"):
                with contextlib.suppress(json.JSONDecodeError):
                    data = json.loads(line)

            marker_text = _failure_marker_text(raw_line, data)
            if _handle_usage_limit(marker_text, process, label, backend):
                log_file.write(raw_line)
                log_file.flush()
                break
            if _handle_auth_error(marker_text, process, label, backend):
                log_file.write(raw_line)
                log_file.flush()
                break
            if _handle_overloaded(marker_text, process, label, backend):
                log_file.write(raw_line)
                log_file.flush()
                break
            # Not a `break`: the CLI keeps streaming (and exits 0) after this — the rest of
            # its output still belongs in the log, and only the flag matters downstream.
            _handle_background_terminated(marker_text, label, backend)
            if data is not None:
                readable = backend.parse_line(data)
                if readable:
                    log_file.write(readable + "\n")
                    log_file.flush()
                    _apply_done_signal(readable, done_event)
                if on_json_event:
                    on_json_event(data)
            else:
                log_file.write(raw_line)
                log_file.flush()

        process.wait()
        return process.returncode


def _run_backend_session(
    backend: Backend,
    prompt: str,
    model: str,
    log_prefix: str,
    banner_label: str,
    *,
    resume_session_id: str | None = None,
    reasoning_effort: str = "",
    progress_tag: str | None = None,
    on_json_event: Callable[[dict], None] | None = None,
    extra_progress_fn: Callable[[], str] | None = None,
    pre_banner_extra: Callable[[], None] | None = None,
) -> tuple[int, Path]:
    """Shared wrapper for every backend-invoking command: writes the startup banner, shows
    a live `\\r` progress line (elapsed time + row count, optionally with a caller-supplied
    tag/suffix), streams the session via `_stream_backend_process`, and always tears the
    progress thread down cleanly. Returns (exit_code, log_path); exit_code is -1 if the
    backend's executable could not even be found/started (the error is written to the log)."""
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"{log_prefix}_{timestamp}.txt"

    start_time = datetime.now()
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    _banner(f"[{start_str}] {banner_label} | backend: {backend.label} | log: {log_path.name}")
    if pre_banner_extra:
        pre_banner_extra()
    if SHOW_PROMPT:
        print(f"PROMPT: {prompt}", flush=True)

    log(f"{banner_label} — backend: {backend.label} — log: {log_path.name}", to_console=False)
    log(f"Prompt:\n{prompt}", to_console=False)

    exit_code = -1
    row_count = [0]
    session_done = threading.Event()
    progress_thread: threading.Thread | None = None

    def _display_progress() -> None:
        # When stdout is piped (e.g. the dashboard runs this as a subprocess), the
        # parent reads line-by-line and only sees a "\r"-only update once a real "\n"
        # is eventually written — for a long session that means the parent (and thus
        # the dashboard) sees nothing until the run finishes. Only use the in-place
        # "\r" overwrite on a real terminal; otherwise emit each tick as its own line
        # so a piped reader gets live progress.
        is_tty = sys.stdout.isatty()
        while not session_done.wait(timeout=1.0):
            now = datetime.now()
            elapsed_str = str(now - start_time).split(".")[0]
            time_str = now.strftime("%H:%M:%S")
            tag_part = f" [{progress_tag}]" if progress_tag else ""
            extra = extra_progress_fn() if extra_progress_fn else ""
            line = f"[{time_str}]{tag_part} [{elapsed_str}] [{row_count[0]} rows]{extra}"
            if is_tty:
                print(f"\r{line}   ", end="", flush=True)
            else:
                print(line, flush=True)

    try:
        cmd, stdin_text = prepare_backend_invocation(backend, model, resume_session_id, prompt, log_path, reasoning_effort)

        progress_thread = threading.Thread(target=_display_progress, daemon=True)
        progress_thread.start()

        exit_code = _stream_backend_process(backend, cmd, stdin_text, log_path, banner_label, row_count, on_json_event)

    except Exception as e:
        log(f"Error running [{banner_label}]: {e}", to_console=False)
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n\n[agent-runner error] {e}\n")
    finally:
        session_done.set()
        if progress_thread is not None:
            progress_thread.join(timeout=2.0)
        print(flush=True)  # end the \r progress line

    return exit_code, log_path



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
    epic["blocked_reason"] = reason
    save_config(config)
    log(f"QA [{session_label}] — {reason}\n"
        "Review the QA reports listed above, fix the underlying conflict between them, then run "
        "`tempa implement --reset-failed` (or click Continue Implementation) to retry.")
    notify_attention(
        AttentionEventType.QA_OSCILLATION_DETECTED,
        "QA",
        f"{session_label} keeps failing QA in circles",
        "Review the QA reports for the rounds listed below — each round's fix is undoing an "
        "earlier one. Resolve that, then run `tempa implement --reset-failed`.",
        epic=session_label,
        log_path=log_path,
        details={"reason": reason, "qa_fail_rounds": sum(
            1 for entry in (epic.get("qa_history") or []) if entry.get("verdict") == VERDICT_FAIL
        )},
    )
    _state.stop_event.set()


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
        if reconcile_qa_passed_features_and_log(config):
            save_config(config)

        # Record this round's verdict and check the epic's QA history for a loop before anything
        # else reads its status — a trip rewrites it to "failed".
        _record_qa_verdict_and_guard(config, index, session_label, log_path)

        epic = config["epic"][index]
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
