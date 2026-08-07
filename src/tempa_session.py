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
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tempa_backend import AUTONOMOUS_SYSTEM_PROMPT, Backend, get_backend_def, resolve_exe
from tempa_config import (
    WORKING_DIR,
    get_backend,
    get_logs_dir,
    get_model,
    get_qa_dir,
    get_reasoning_effort,
    get_server_overloaded_retry_wait_sec,
    get_usage_limit_heartbeat_sec,
    get_usage_limit_retry_wait_sec,
    load_config,
    save_config,
    set_epic_session_id,
)
from tempa_logging import SHOW_PROMPT, _banner, _print_log_tail, _state, log
from tempa_notifications import AttentionEventType, notify_attention


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
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            cwd=str(WORKING_DIR),
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

        for raw_line in process.stdout:
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
            if data is not None:
                readable = backend.parse_line(data)
                if readable:
                    log_file.write(readable + "\n")
                    log_file.flush()
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
    """Log SUCCEEDED / usage-limit-stopped / overload-paused / FAILED (with a one-time log
    tail) for a finished session. Returns True iff exit_code == 0 and neither a usage limit
    nor a server overload was hit."""
    if _state.auth_error_hit:
        log(f"{label} stopped — authentication failed (see message above).")
        return False
    if _state.usage_limit_hit:
        log(f"{label} stopped — usage limit reached.{usage_limit_note}")
        return False
    if _state.server_overloaded_hit:
        log(f"{label} paused — backend API overloaded (will retry automatically).")
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
        # A usage-limit, auth-error, or server-overload stop is not a real epic failure:
        # leave status untouched so the epic can be resumed once the limit resets / auth is
        # fixed / the backend's API recovers.
        if (
            exit_code != 0
            and not _state.usage_limit_hit
            and not _state.auth_error_hit
            and not _state.server_overloaded_hit
        ):
            # Only mark failed — "done"/"pending" is set by the AI session itself
            config = load_config()
            config["epic"][index]["status"] = "failed"
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
        _state.running_thread = None
        _state.running_index = None


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
