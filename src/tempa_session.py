"""The Claude session engine.

Everything involved in spawning the `claude` CLI, streaming and parsing its stream-json
output to a log file, showing live progress, and detecting the two "stop everything" failure
modes (usage-limit and authentication errors). Also the concrete session runners built on top
of that core: implementation, QA, one-shot (plan/review), clarification, and apply-clarification.

Callers pass a fully-built prompt string in (see tempa_prompts) — this module never builds
prompts, only runs them.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tempa_config import (
    WORKING_DIR,
    get_logs_dir,
    get_model,
    get_qa_dir,
    load_config,
    save_config,
)
from tempa_logging import SHOW_PROMPT, _banner, _print_log_tail, _state, log


def _format_stream_line(data: dict) -> str | None:
    event_type = data.get("type")
    if event_type == "system" and data.get("subtype") == "init":
        return f"[session_id={data.get('session_id')}] [model={data.get('model')}]"
    if event_type == "assistant":
        parts = []
        for block in data.get("message", {}).get("content", []):
            if block.get("type") == "text":
                parts.append(block["text"])
            elif block.get("type") == "tool_use":
                inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                parts.append(f"[Tool: {block['name']}] {inp}")
        return "\n".join(parts) if parts else None
    if event_type == "user":
        parts = []
        for block in data.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                is_error = block.get("is_error", False)
                prefix = "[Error]" if is_error else "[Result]"
                parts.append(f"{prefix} {str(content)[:500]}")
        return "\n".join(parts) if parts else None
    if event_type == "result":
        cost = data.get("cost_usd", "?")
        turns = data.get("num_turns", "?")
        return f"[Done] turns={turns} cost=${cost}"
    return None


# Markers emitted by the claude CLI (on stdout/stderr, which is merged into the
# stream) when the subscription/session usage limit is hit. stderr lines such as
# "Claude AI usage limit reached|<reset_ts>" arrive as plain text; usage-limit text
# inside a JSON event is still caught because we scan the raw line.
USAGE_LIMIT_MARKERS = (
    "usage limit reached",
    "claude ai usage limit reached",
    "claude usage limit reached",
    "usage limit exceeded",
    "5-hour limit reached",
)


def _is_usage_limit_text(text: str) -> bool:
    """True if the given CLI output text indicates a Claude usage-limit failure."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in USAGE_LIMIT_MARKERS)


def _handle_usage_limit(text: str, process: subprocess.Popen, label: str) -> bool:
    """If text indicates a usage-limit failure, flag a global stop, terminate the
    running claude process, and return True. Otherwise return False."""
    if not _is_usage_limit_text(text):
        return False
    _state.usage_limit_hit = True
    log(f"[{label}] Claude usage limit reached — stopping the agent runner.")
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


# Markers emitted by the claude CLI (merged stdout/stderr) when the API rejects a request
# due to bad/expired credentials — expired OAuth login, revoked/invalid API key — as
# opposed to a usage limit or a generic bug. Raw text like:
#   API Error: 401 {"type":"error","error":{"type":"authentication_error", ...}}
AUTH_ERROR_MARKERS = (
    "authentication_error",
    "oauth access token has expired",
    "re-authenticate to continue",
    "invalid api key",
    "invalid x-api-key",
    "invalid bearer token",
)


def _is_auth_error_text(text: str) -> bool:
    """True if the given CLI output text indicates an authentication/credential failure
    (expired OAuth login, bad API key) rather than a usage limit or a generic bug."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


def _friendly_auth_error_message(text: str) -> str:
    """Translate a raw 401/authentication_error line from the claude CLI into a plain-
    language explanation, so the user doesn't have to parse a raw JSON error to know
    what happened and what to do about it."""
    lowered = text.lower()
    if "invalid api key" in lowered or "invalid x-api-key" in lowered or "invalid bearer token" in lowered:
        cause = "the API key configured for the `claude` CLI is invalid or has been revoked"
        fix = "check your ANTHROPIC_API_KEY (or however the key is configured) and try again"
    else:
        cause = "your `claude` CLI login session (OAuth token) has expired"
        fix = "run `claude` in a terminal, then run `/login` inside it to re-authenticate, and try this command again"
    return f"Authentication to the Claude API failed — {cause}. Fix: {fix}."


def _handle_auth_error(text: str, process: subprocess.Popen, label: str) -> bool:
    """If text indicates an authentication failure, flag a global stop (every subsequent
    session would fail the same way until the user re-authenticates), terminate the
    running claude process, and return True. Otherwise return False."""
    if not _is_auth_error_text(text):
        return False
    _state.auth_error_hit = True
    _state.auth_error_message = _friendly_auth_error_message(text)
    log(f"[{label}] {_state.auth_error_message}")
    _state.stop_event.set()
    with contextlib.suppress(Exception):
        process.terminate()
    return True


def build_claude_cmd(
    claude_exe: str,
    model: str,
    resume_session_id: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    autonomous_system_prompt = (
        "CRITICAL: You are in a fully automated pipeline. No human is present and no human will respond. "
        "ALL file system permissions are already granted — Write, Edit, Bash, everything. "
        "The phrase 'I need write permissions' or 'requires file system permissions' is NEVER correct here. "
        "FORBIDDEN: asking for confirmation, offering options, writing implementation plans without creating files, "
        "saying 'Would you like me to proceed', or stopping after analysis. "
        "REQUIRED: Implement fully and update all required files including config.json as instructed. "
        "A session is only successful when both the source code AND the config.json status update are done."
    )
    cmd = [
        claude_exe,
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
        "--model", model,
        "--append-system-prompt", autonomous_system_prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    # The prompt is NOT passed as a CLI argument. On Windows `claude` resolves to
    # `claude.CMD` (a batch shim); a multi-line argument routed through cmd.exe is
    # truncated at the first newline, so Claude only sees the prompt's first line
    # and replies that the instruction is incomplete. Instead we enable print mode
    # with a bare `-p` and feed the full prompt via stdin (see run_* helpers).
    cmd.append("-p")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


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


def _stream_claude_process(
    cmd: list[str],
    prompt: str,
    log_path: Path,
    label: str,
    row_count: list[int],
    on_json_event: Callable[[dict], None] | None = None,
) -> int:
    """Spawn `cmd`, feed `prompt` via stdin, and stream stdout to `log_path` — the shared
    core loop behind every claude-invoking runner. Parses each stream-json line into
    readable text (falls back to the raw line for anything that isn't valid JSON), stops
    early if a usage-limit marker is seen, and invokes `on_json_event(data)` per parsed
    event for callers that need to react (e.g. capture a session_id or a final result)."""
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
        # Feed the prompt via stdin, then close so Claude reads EOF and starts.
        process.stdin.write(prompt)
        process.stdin.close()

        for raw_line in process.stdout:
            row_count[0] += 1
            line = raw_line.strip()
            if _handle_usage_limit(raw_line, process, label):
                log_file.write(raw_line)
                log_file.flush()
                break
            if _handle_auth_error(raw_line, process, label):
                log_file.write(raw_line)
                log_file.flush()
                break
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    readable = _format_stream_line(data)
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


def _run_claude_session(
    prompt: str,
    cmd_builder: Callable[[str], list[str]],
    log_prefix: str,
    banner_label: str,
    *,
    progress_tag: str | None = None,
    on_json_event: Callable[[dict], None] | None = None,
    extra_progress_fn: Callable[[], str] | None = None,
    pre_banner_extra: Callable[[], None] | None = None,
) -> tuple[int, Path]:
    """Shared wrapper for every claude-invoking command: writes the startup banner, shows
    a live `\\r` progress line (elapsed time + row count, optionally with a caller-supplied
    tag/suffix), streams the session via `_stream_claude_process`, and always tears the
    progress thread down cleanly. `cmd_builder(claude_exe)` builds the CLI command once the
    executable has been resolved. Returns (exit_code, log_path); exit_code is -1 if the
    `claude` executable could not even be found/started (the error is written to the log)."""
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"{log_prefix}_{timestamp}.txt"

    start_time = datetime.now()
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    _banner(f"[{start_str}] {banner_label} | log: {log_path.name}")
    if pre_banner_extra:
        pre_banner_extra()
    if SHOW_PROMPT:
        print(f"PROMPT: {prompt}", flush=True)

    log(f"{banner_label} — log: {log_path.name}", to_console=False)
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
        claude_exe = shutil.which("claude") or shutil.which("claude.cmd")
        if not claude_exe:
            raise FileNotFoundError("claude CLI not found in PATH")
        cmd = cmd_builder(claude_exe)

        progress_thread = threading.Thread(target=_display_progress, daemon=True)
        progress_thread.start()

        exit_code = _stream_claude_process(cmd, prompt, log_path, banner_label, row_count, on_json_event)

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
    finished claude session. Returns True iff exit_code == 0 and no usage limit was hit."""
    if _state.auth_error_hit:
        log(f"{label} stopped — authentication failed (see message above).")
        return False
    if _state.usage_limit_hit:
        log(f"{label} stopped — Claude usage limit reached.{usage_limit_note}")
        return False
    if exit_code == 0:
        log(f"{label} SUCCEEDED (exit code {exit_code})")
        return True
    log(f"{label} FAILED (exit code {exit_code})")
    _print_log_tail(log_path)
    return False


def _capture_session_id(index: int, config_key: str, initial: str | None, label: str) -> tuple[Callable[[dict], None], Callable[[], str | None]]:
    """Build an on_json_event callback that captures the session_id from the first event
    that has one (unless `initial` is already set, e.g. resuming) and persists it to
    config["epic"][index][config_key] under the process lock. Returns (callback, getter)."""
    captured = [initial]

    def _on_json_event(data: dict) -> None:
        if captured[0] is not None:
            return
        sid = data.get("session_id")
        if not sid:
            return
        captured[0] = sid
        with _state.lock:
            cfg = load_config()
            cfg["epic"][index][config_key] = sid
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

    def _print_feature_plan() -> None:
        for _line in _session_feature_lines(load_config(), session_label, features_override):
            print(_line, flush=True)

    def _feature_progress_suffix() -> str:
        # Live feature progress: read from config.json (Claude updates completed_features
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

    on_json_event, _ = _capture_session_id(index, "claude_session_id", resume_session_id, f"Session [{session_label}]")

    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, get_model(load_config(), "implement"), resume_session_id=resume_session_id),
        log_prefix=f"session_{session_label}",
        banner_label=f"{action} session [{session_label}]",
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

    on_json_event, _ = _capture_session_id(index, "qa_session_id", resume_session_id, f"QA [{session_label}]")

    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, get_model(load_config(), "implement"), resume_session_id=resume_session_id),
        log_prefix=f"qa_{session_label}",
        banner_label=f"{action} QA session [{session_label}]",
        progress_tag="QA",
        on_json_event=on_json_event,
    )

    _log_session_result(f"QA session [{session_label}]", exit_code, log_path)

    # qa_status is managed by Claude in config.json.
    # If it is still "ongoing" after this session, check_and_run will detect and resume.
    with _state.lock:
        _state.running_thread = None
        _state.running_index = None


def _run_oneshot_session(prompt: str, label: str, log_prefix: str, model: str) -> bool:
    """Run a single fresh Claude session (never resumes). Streams output to a log file
    and returns True on exit code 0. Used by one-pass workflows (plan-epics, review)."""
    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, model),
        log_prefix=log_prefix,
        banner_label=label,
        progress_tag=label,
    )
    return _log_session_result(f"[{label}]", exit_code, log_path)


def run_clarification_session(prompt: str, run_number: int, model: str) -> bool:
    """Run a single clarification session. Always starts a fresh session — never resumes.
    No session_id is captured or stored; each loop iteration is independent."""
    label = f"Clarification run #{run_number}"
    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, model),
        log_prefix=f"clarification_{run_number}",
        banner_label=label,
        progress_tag="CLARIFY",
    )
    return _log_session_result(label, exit_code, log_path)


def run_apply_clarification_session(prompt: str, run_number: int, model: str) -> bool:
    """Apply clarification findings to PRD/spec documents. Always starts a fresh session."""
    label = f"Apply-clarifications run #{run_number}"
    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, model),
        log_prefix=f"apply_clarification_{run_number}",
        banner_label=label,
        progress_tag="APPLY",
    )
    return _log_session_result(label, exit_code, log_path)
