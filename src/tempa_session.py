"""The agent-runner session engine, shared by every backend (Claude Code, GitHub Copilot
CLI, OpenAI Codex CLI).

Everything involved in spawning a backend's CLI, feeding it a prompt, streaming and parsing
its output to a log file, showing live progress, and detecting the two "stop everything"
failure modes (usage-limit and authentication errors) lives here — generically, driven by
the active `tempa_backend.Backend`. Also the concrete session runners built on top of that
core: implementation, QA, one-shot (plan/review), clarification, and apply-clarification.

Callers pass a fully-built prompt string in (see tempa_prompts) — this module never builds
prompts, only runs them.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
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
    load_config,
    save_config,
    set_epic_session_id,
)
from tempa_logging import SHOW_PROMPT, _banner, _print_log_tail, _state, log


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
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


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
        return backend.build_cmd(exe, model, resume_session_id, prompt_arg), ""

    return backend.build_cmd(exe, model, resume_session_id, None), full_prompt


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
            if _handle_usage_limit(raw_line, process, label, backend):
                log_file.write(raw_line)
                log_file.flush()
                break
            if _handle_auth_error(raw_line, process, label, backend):
                log_file.write(raw_line)
                log_file.flush()
                break
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    readable = backend.parse_line(data)
                    if readable:
                        log_file.write(readable + "\n")
                        log_file.flush()
                    if on_json_event:
                        on_json_event(data)
                except json.JSONDecodeError:
                    log_file.write(raw_line)
                    log_file.flush()
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
        cmd, stdin_text = prepare_backend_invocation(backend, model, resume_session_id, prompt, log_path)

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
    """Log SUCCEEDED / usage-limit-stopped / FAILED (with a one-time log tail) for a
    finished session. Returns True iff exit_code == 0 and no usage limit was hit."""
    if _state.auth_error_hit:
        log(f"{label} stopped — authentication failed (see message above).")
        return False
    if _state.usage_limit_hit:
        log(f"{label} stopped — usage limit reached.{usage_limit_note}")
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
        on_json_event=on_json_event,
        extra_progress_fn=_feature_progress_suffix,
        pre_banner_extra=_print_feature_plan,
    )

    _log_session_result(
        f"Session [{session_label}]", exit_code, log_path,
        usage_limit_note=" (epic left as on_progress so it can be resumed once the limit resets).",
    )

    with _state.lock:
        # A usage-limit or auth-error stop is not a real epic failure: leave status
        # untouched so the epic can be resumed once the limit resets / auth is fixed.
        if exit_code != 0 and not _state.usage_limit_hit and not _state.auth_error_hit:
            # Only mark failed — "done"/"pending" is set by the AI session itself
            config = load_config()
            config["epic"][index]["status"] = "failed"
            save_config(config)
            log(f"Session [{session_label}] marked as failed")
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
        progress_tag="QA",
        on_json_event=on_json_event,
    )

    _log_session_result(f"QA session [{session_label}]", exit_code, log_path)

    # qa_status is managed by the agent in config.json.
    # If it is still "ongoing" after this session, check_and_run will detect and resume.
    with _state.lock:
        _state.running_thread = None
        _state.running_index = None


def _run_oneshot_session(prompt: str, label: str, log_prefix: str, backend: Backend, model: str) -> bool:
    """Run a single fresh session (never resumes) against `backend`. Streams output to a
    log file and returns True on exit code 0. Used by one-pass workflows (plan-epics,
    review)."""
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=log_prefix,
        banner_label=label,
        progress_tag=label,
    )
    return _log_session_result(f"[{label}]", exit_code, log_path)


def run_clarification_session(prompt: str, run_number: int, backend: Backend, model: str) -> bool:
    """Run a single clarification session against `backend`. Always starts a fresh
    session — never resumes. No session_id is captured or stored; each loop iteration is
    independent."""
    label = f"Clarification run #{run_number}"
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=f"clarification_{run_number}",
        banner_label=label,
        progress_tag="CLARIFY",
    )
    return _log_session_result(label, exit_code, log_path)


def run_apply_clarification_session(prompt: str, run_number: int, backend: Backend, model: str) -> bool:
    """Apply clarification findings to PRD/spec documents against `backend`. Always
    starts a fresh session."""
    label = f"Apply-clarifications run #{run_number}"
    exit_code, log_path = _run_backend_session(
        backend,
        prompt,
        model,
        log_prefix=f"apply_clarification_{run_number}",
        banner_label=label,
        progress_tag="APPLY",
    )
    return _log_session_result(label, exit_code, log_path)
