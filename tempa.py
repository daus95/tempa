from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from dashboard_ui import file_answer_status, run_dashboard

# Ensure UTF-8 output on Windows consoles with non-unicode code pages
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
WORKING_DIR = SCRIPT_DIR.parent
LOGS_DIR = SCRIPT_DIR / "logs"
VERIFY_DIR = SCRIPT_DIR / "verify"
QA_DIR = SCRIPT_DIR / "qa"
# Prompt templates live here, one .md file per prompt (readable, easy to edit).
# Loaded via load_prompt(); no longer stored in config.json.
PROMPT_DIR = SCRIPT_DIR / "prompt"
POLL_INTERVAL_SEC = 60

# The prompt sent to Claude is NOT shown on the console unless the user adds
# --show-prompt. (The prompt is always recorded to the log file regardless.)
SHOW_PROMPT = "--show-prompt" in sys.argv

# Default working-folder layout. "root" MUST be an absolute path; every other
# entry is a path RELATIVE to root. Stored under the "workspace" key in config.json.
DEFAULT_WORKSPACE = {
    # Absolute path to the folder that contains all the sub-folders below.
    "root": "",
    # Current documentation of the application being worked on.
    "docs": "docs",
    # Architecture Decision Records produced while building the application.
    "adr": "adr",
    # NEW specifications to be worked on (current app specs live in docs/).
    "specs": "specs",
    # Application implementation source code.
    "apps": "apps",
    # Infrastructure scripts (e.g. docker compose).
    "infra": "infra",
    # Sub-folders holding past specification files no longer in use.
    "archive": "archive",
}

# Human-readable labels for each workspace folder, used in CLI output.
WORKSPACE_LABELS = {
    "root": "Root folder (absolute)",
    "docs": "Documentation folder",
    "adr": "ADR folder",
    "specs": "Specs folder",
    "apps": "Applications folder",
    "infra": "Infrastructure folder",
    "archive": "Archive folder",
}

# AI model per harness stage. Stored under the "models" key in config.json.
# - clarify  : PRD clarification session (clarify)
# - plan     : epic/feature/task planning session (run automatically by implement / implement --replan)
# - implement: implementation session (implement), including QA and verify
DEFAULT_MODELS = {
    "clarify": "claude-opus-4-8",
    "plan": "claude-sonnet-5",
    "implement": "claude-sonnet-5",
}

# Friendly aliases → full model id, so users can type e.g. "opus-4.8" or "sonnet-5".
MODEL_ALIASES = {
    "opus-4.8": "claude-opus-4-8",
    "opus": "claude-opus-4-8",
    "sonnet-5": "claude-sonnet-5",
    "sonnet": "claude-sonnet-5",
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "haiku": "claude-haiku-4-5-20251001",
    "fable-5": "claude-fable-5",
    "fable": "claude-fable-5",
}

class _RunnerState:
    """Mutable cross-thread state shared by the poll loop (check_and_run) and whichever
    session thread is currently running. Grouped into one object instead of loose globals
    so every read/write site is explicit about what it's touching; `lock` guards
    `running_thread`/`running_index` plus every read-modify-write of config.json."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running_thread: threading.Thread | None = None
        self.running_index: int | None = None
        self.stop_event = threading.Event()
        self.all_done = False
        self.usage_limit_hit = False
        self.auth_error_hit = False
        self.auth_error_message = ""


_state = _RunnerState()

# Process-level log file (all log() output goes here)
_process_log_path: Path | None = None
_process_log_lock = threading.Lock()


def _init_process_log() -> None:
    global _process_log_path
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _process_log_path = LOGS_DIR / f"process_{timestamp}.txt"


def _write_to_process_log(line: str) -> None:
    if _process_log_path is None:
        return
    try:
        with _process_log_lock:
            with open(_process_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def log(message: str, to_console: bool = True) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    if to_console:
        print(line, flush=True)
    _write_to_process_log(line)


def _banner(title: str) -> None:
    """Print a single-line banner (replaces the old multi-line '=' separator blocks)."""
    print(f"== {title} ==", flush=True)


def _print_log_tail(log_path: Path, lines: int = 20) -> None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = text.strip().splitlines()[-lines:]
        log(f"--- last {lines} lines of {log_path.name} ---")
        for line in tail:
            print(f"  {line}", flush=True)
        log("--- end ---")
    except Exception as e:
        log(f"Could not read log file: {e}")


def _hyperlink(path: Path) -> str:
    """Wrap an absolute path in an OSC 8 terminal hyperlink (file:// URI) so terminals
    that support it (e.g. Windows Terminal) render it clickable — Ctrl+Click opens the
    file. Falls back to the plain path when stdout is not a TTY (piped/redirected) or the
    path cannot be turned into a URI."""
    text = str(path)
    try:
        if not sys.stdout.isatty():
            return text
        uri = Path(path).as_uri()  # requires an absolute path; percent-encodes
    except (ValueError, AttributeError):
        return text
    esc = "\033"
    return f"{esc}]8;;{uri}{esc}\\{text}{esc}]8;;{esc}\\"


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
    try:
        process.terminate()
    except Exception:
        pass
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
    try:
        process.terminate()
    except Exception:
        pass
    return True


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def load_prompt(name: str, fallback: str = "") -> str:
    """Load a prompt template from PROMPT_DIR/<name>.md.

    Returns the file content verbatim (with its ${...} placeholders intact).
    If the file is missing, returns `fallback`; if there is no fallback either,
    logs an error and returns an empty string so the failure is visible.
    """
    path = PROMPT_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    if fallback:
        return fallback
    log(f"ERROR: prompt template '{name}' not found at {path}")
    return ""


def get_workspace(config: dict) -> dict:
    """Return the workspace config merged over DEFAULT_WORKSPACE, so any missing
    key falls back to its default."""
    workspace = dict(DEFAULT_WORKSPACE)
    workspace.update(config.get("workspace", {}))
    return workspace


def resolve_workspace_paths(config: dict) -> dict:
    """Resolve every workspace folder to an absolute path. `root` is returned as-is;
    each other folder is joined onto root. Returns {} if root is not configured."""
    workspace = get_workspace(config)
    root = workspace.get("root", "")
    if not root:
        return {}
    root_path = Path(root)
    resolved = {"root": str(root_path)}
    for key in DEFAULT_WORKSPACE:
        if key == "root":
            continue
        resolved[key] = str(root_path / workspace[key])
    return resolved


def resolve_source_path(config: dict, value: str) -> str:
    """Resolve a single `sources` value to an absolute path.

    - Empty value: returned as-is.
    - Already-absolute path: returned as-is (backward compatible).
    - Relative path: joined onto workspace.root. If root is not configured, the
      relative value is returned unchanged.
    """
    if not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    root = get_workspace(config).get("root", "")
    return str(Path(root) / value) if root else value


def get_sources(config: dict) -> dict:
    """Return the `sources` dict with every value resolved to an absolute path
    relative to workspace.root (see resolve_source_path). Use this everywhere
    instead of reading config["sources"] directly, so relative source paths work."""
    raw = config.get("sources", {})
    return {key: resolve_source_path(config, value) for key, value in raw.items()}


def resolve_specs_dir(config: dict) -> Path:
    """Return the absolute path of the specifications folder (workspace.specs).

    Mirrors how the agent runner resolves relative paths: joined onto workspace.root
    when configured, otherwise onto WORKING_DIR (where the agent is run), so `spec`
    points at the same folder the rest of the pipeline reads/writes."""
    workspace = get_workspace(config)
    specs_rel = workspace.get("specs") or "specs"
    root = workspace.get("root")
    if root:
        return Path(root) / specs_rel
    specs_path = Path(specs_rel)
    return specs_path if specs_path.is_absolute() else WORKING_DIR / specs_rel


def _resolve_prd_dir(config: dict) -> Path:
    """Return the absolute path of the PRD folder (sources.prd), falling back to
    <specs>/prd when sources.prd isn't configured. Shared by every entry point that
    opens the dashboard's Specification section."""
    prd_dir_str = get_sources(config).get("prd", "")
    return Path(prd_dir_str) if prd_dir_str else resolve_specs_dir(config) / "prd"


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


def build_prompt(template: str, parameters: dict) -> str:
    result = template
    for key, value in parameters.items():
        result = result.replace(f"${{{key}}}", value)
    return result


def _resolve_template_params(config: dict, epic_name: str) -> dict:
    """Build the full substitution dict: epic_name + sources + config_path."""
    sources = get_sources(config)
    sources_str = "\n".join(sources.values())
    params = {
        "epic": epic_name,
        "sources": sources_str,
        "config_path": str(CONFIG_PATH),
    }
    for key, value in sources.items():
        params[f"sources.{key}"] = value
    return params


def _build_features_block(config: dict, epic: str) -> str:
    """Build a text block showing features status (done/require_fixing/pending) for the given epic."""
    session_features: list[dict] = next(
        (s.get("features", []) for s in (config.get("epic") or [])
         if s.get("epic_name") == epic),
        [],
    )
    if not session_features:
        return ""

    done = [f for f in session_features if f["status"] == "done"]
    require_fixing = [f for f in session_features if f["status"] == "require_fixing"]
    pending = [f for f in session_features if f["status"] == "pending"]

    lines = ["FEATURES FOR THIS EPIC:"]
    if done:
        lines.append("Already done (DO NOT re-implement):")
        lines.extend(f"  ✅ {f['id']} — {f['name']}" for f in done)
    if require_fixing:
        lines.append("Needs fixing — already implemented but QA findings were found (read the QA report):")
        lines.extend(f"  🔧 {f['id']} — {f['name']}" for f in require_fixing)
    if pending:
        lines.append("Needs implementing (never built):")
        lines.extend(f"  ⬜ {f['id']} — {f['name']}" for f in pending)
    lines.append("")
    return "\n".join(lines) + "\n"


def _build_qa_report_section(config: dict, epic: str) -> str:
    """Return a prompt section pointing to previous QA findings if a report file exists."""
    epic_entry = next((s for s in (config.get("epic") or []) if s.get("epic_name") == epic), None)
    if not epic_entry:
        return ""
    qa_report_filename = epic_entry.get("qa_report_filename", "")
    if not qa_report_filename or not Path(qa_report_filename).exists():
        return ""
    return (
        f"PREVIOUS QA FINDINGS — MUST BE READ BEFORE IMPLEMENTATION:\n"
        f"Read the following QA report to understand the findings that must be fixed:\n"
        f"  {qa_report_filename}\n"
        f"All ❌ and ⚠️ findings in that report MUST be fixed in this implementation session.\n\n"
    )


def build_session_prompt(
    config: dict,
    epic_name: str,
    is_continuation: bool = False,
    features_override: int | None = None,
) -> str:
    """Build a full prompt for a new session, incorporating continuation and features_per_session."""
    params = _resolve_template_params(config, epic_name)
    epic = epic_name

    if is_continuation:
        template = load_prompt("continuation") or load_prompt("implementation")
    else:
        template = load_prompt("implementation")

    # Prepend critical config update rule so it's not missed at the end of a long session
    features_per_session = features_override if features_override is not None else config.get("features_per_session")

    features_block = _build_features_block(config, epic)

    config_path_note = (
        f"AGENT CONFIG FILE: {CONFIG_PATH}\n"
        f"Always use Read first, then Edit. Do not use Glob — use the absolute path above.\n"
    )
    if features_per_session:
        config_rule = (
            f"MANDATORY RULE — DO NOT SKIP:\n"
            f"Implement or fix the features from the 🔧 and ⬜ list above, one at a time, in order.\n"
            f"Every time you finish 1 feature:\n"
            f"  1. READ {CONFIG_PATH} then EDIT:\n"
            f"     a. Find the entry with \"epic_name\": \"{epic}\" in the \"epic\" array\n"
            f"     b. In that entry's \"features\" array, find the object whose \"id\" = the feature just finished\n"
            f"     c. Change its \"status\" to \"done\"\n"
            f"     d. Increment \"completed_features\" by 1\n"
            f"     e. If ALL features now have status \"done\":\n"
            f"        ALSO change \"status\" AT THE EPIC LEVEL (the field directly on that entry,\n"
            f"        not the \"status\" inside the \"features\" array) to \"done\"\n"
            f"\n"
            f"Limit for this session: at most {features_per_session} feature(s).\n"
            f"Stop once you reach the limit (or all features are done).\n"
        )
    else:
        config_rule = (
            f"MANDATORY RULE — DO THIS EVERY TIME YOU FINISH 1 FEATURE (before moving to the next one):\n"
            f"A 🔧 feature means it's already implemented but has QA findings — fix it per the QA report.\n"
            f"A ⬜ feature means it was never built — implement it from scratch.\n"
            f"  1. READ {CONFIG_PATH} then EDIT:\n"
            f"     a. Find the entry with \"epic_name\": \"{epic}\" in the \"epic\" array\n"
            f"     b. In that entry's \"features\" array, find the object whose \"id\" = the feature just finished\n"
            f"     c. Change its \"status\" to \"done\"\n"
            f"     d. Increment \"completed_features\" by 1\n"
            f"     ⚠ \"status\" AT THE EPIC LEVEL (the field directly on the entry, not inside the \"features\" array)\n"
            f"       is the overall epic status — DO NOT change it until all features are done\n"
            f"\n"
            f"MANDATORY RULE — AFTER THE ENTIRE EPIC IS DONE:\n"
            f"  READ {CONFIG_PATH} then EDIT: change \"status\" AT THE EPIC LEVEL\n"
            f"  (the field directly on the entry \"epic_name\": \"{epic}\", not \"status\" inside the \"features\" array)\n"
            f"  to \"done\".\n"
            f"  ⚠ CRITICAL: If the epic's \"status\" is not changed to \"done\",\n"
            f"    the agent runner will keep restarting this session endlessly.\n"
        )

    qa_report_section = _build_qa_report_section(config, epic)
    prompt = build_prompt(template, params) + "\n\n" + features_block + qa_report_section + config_rule + "\n" + config_path_note

    return prompt


def build_qa_prompt(config: dict, epic_name: str, qa_output_file: Path, is_continuation: bool = False) -> str:
    params = _resolve_template_params(config, epic_name)
    params["qa_output_file"] = str(qa_output_file)
    if is_continuation:
        template = load_prompt("qa_continuation") or load_prompt("qa")
    else:
        template = load_prompt("qa")
    return build_prompt(template, params)


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
        lines.append(f"Processed this session ({len(batch)} feature(s)):")
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
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"{log_prefix}_{timestamp}.txt"

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
        while not session_done.wait(timeout=1.0):
            now = datetime.now()
            elapsed_str = str(now - start_time).split(".")[0]
            time_str = now.strftime("%H:%M:%S")
            tag_part = f" [{progress_tag}]" if progress_tag else ""
            extra = extra_progress_fn() if extra_progress_fn else ""
            print(f"\r[{time_str}]{tag_part} [{elapsed_str}] [{row_count[0]} rows]{extra}   ", end="", flush=True)

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
        # whenever a feature finishes). Ignore read errors (e.g. config is being written)
        # — display without feature info for that iteration.
        try:
            cfg = load_config()
            epic = next((s for s in (cfg.get("epic") or []) if s.get("epic_name") == session_label), None)
            if epic:
                return f" [feat {epic.get('completed_features', 0)}/{epic.get('total_features', 0)}]"
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

    QA_DIR.mkdir(exist_ok=True)
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
                resume_sid = session.get("qa_session_id") or None
                log(f"QA [{label}] was interrupted (qa_status=ongoing) — resuming with session_id: {resume_sid}")

                if not _validate_and_increment_qa_run(config, i, label):
                    save_config(config)
                    return

                QA_DIR.mkdir(exist_ok=True)
                qa_report_filename = session.get("qa_report_filename", "")
                if qa_report_filename:
                    qa_output_file = Path(qa_report_filename)
                else:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    qa_output_file = QA_DIR / f"{label}-qa-{timestamp}.md"
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

                QA_DIR.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                qa_output_file = QA_DIR / f"{label}-qa-{timestamp}.md"

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
                log(f"Halted — session [{label}] at index {i} has failed. Fix it before proceeding.")
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


def run_test() -> None:
    claude_exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_exe:
        raise FileNotFoundError("claude CLI not found in PATH")

    test_file = WORKING_DIR / "permission-test.txt"
    done_file = WORKING_DIR / "permission-test-done.txt"

    for f in (test_file, done_file):
        if f.exists():
            f.unlink()

    test_prompt = (
        f"Execute these exact steps using your file tools, one by one, with no confirmation needed: "
        f"(1) Write the text 'permission test ok' to the file {test_file}. "
        f"(2) Read that file back and confirm the content. "
        f"(3) Delete that file. "
        f"(4) Write the text 'done' to the file {done_file}."
    )

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"test_{timestamp}.txt"

    log(f"Permission test starting — claude: {claude_exe} | log: {log_path.name}")

    # Same pattern as every other session runner: capture the raw --output-format
    # stream-json output and parse it into readable lines (written to the log file)
    # instead of dumping raw JSON straight to the console.
    try:
        exit_code = _stream_claude_process(
            build_claude_cmd(claude_exe, get_model(load_config(), "implement")),
            test_prompt, log_path, "permission test", [0],
        )
    except Exception as e:
        log(f"TEST FAILED — error running claude: {e}")
        return

    if test_file.exists():
        test_file.unlink()

    if _state.auth_error_hit:
        log(f"TEST stopped — authentication failed (see message above; log: {log_path.name})")
    elif _state.usage_limit_hit:
        log(f"TEST stopped — Claude usage limit reached (see log: {log_path.name})")
    elif exit_code != 0:
        log(f"TEST FAILED — claude exited with code {exit_code} (see log: {log_path.name})")
    elif not done_file.exists():
        log(f"TEST FAILED — claude did not complete all steps (done marker missing) (see log: {log_path.name})")
    else:
        done_file.unlink()
        log("TEST PASSED — all steps completed successfully")


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
            if _state.usage_limit_hit:
                log("Plan stopped — Claude usage limit reached.")
                sys.exit(2)
            log("Plan failed — agent runner stopping.")
            sys.exit(1)

    start_time = datetime.now()
    banner_parts = [
        f"Agent Runner started {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"dir={WORKING_DIR}",
        f"poll={POLL_INTERVAL_SEC}s",
    ]
    if _process_log_path:
        banner_parts.append(f"log={_process_log_path.name}")
    if features_override is not None:
        banner_parts.append(f"features/session={features_override}")
    _banner(" | ".join(banner_parts))

    _print_session_plan(load_config(), features_override)

    log(f"Agent runner started — working dir: {WORKING_DIR}", to_console=False)
    log(f"Poll interval: {POLL_INTERVAL_SEC}s | Config: {CONFIG_PATH}", to_console=False)
    if features_override is not None:
        log(f"features_per_session override: {features_override}", to_console=False)

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
            "Re-authenticate the `claude` CLI, then run this command again.")
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Agent runner stopped — Claude usage limit reached. "
            "Run it again once the limit resets.")
        sys.exit(2)
    if _state.all_done:
        log("All epics done. Agent runner stopped successfully.")
        sys.exit(0)
    log("Agent runner stopped due to session failure.")
    sys.exit(1)


def build_clarification_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(CONFIG_PATH),
    }
    return build_prompt(template, params)


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


def build_apply_clarification_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("apply_clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(CONFIG_PATH),
    }
    return build_prompt(template, params)


def build_auto_answer_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("auto_answer")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(CONFIG_PATH),
    }
    return build_prompt(template, params)


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


def _clarification_report_files(folder: Path, since: float) -> list[Path]:
    """Return .md files in `folder` last modified at/after `since` (epoch seconds) —
    i.e. the report files produced/updated by the evaluation that just ran."""
    if not folder.exists():
        return []
    out: list[Path] = []
    for p in sorted(folder.glob("*.md")):
        try:
            if p.stat().st_mtime >= since:
                out.append(p)
        except OSError:
            pass
    return out


def run_clarify_once(noui: bool = False) -> None:
    """Manual clarification — run ONE evaluation pass, report findings + report file(s),
    then suggest the next step based on severity:
      - critical==0 & major==0 : clarification done → suggest moving on to implement (auto plan)
      - critical==0 (major>0)  : suggest answering manually, or finishing with clarify --finalize
      - critical>0             : suggest reviewing/answering manually then clarify again
    Unless `noui` is set, also opens the clarification-answer web UI on the freshly
    written report file(s) so the user can answer right away instead of hand-editing
    the markdown."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    clar_dir.mkdir(parents=True, exist_ok=True)

    _banner(f"Clarify (manual) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    start_ts = time.time() - 1  # small epsilon so freshly-written files are caught
    prompt = build_clarification_prompt(config)
    if not run_clarification_session(prompt, 1, get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify stopped — Claude usage limit reached.")
            sys.exit(2)
        log("Clarification evaluation failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Clarify stopped — Claude usage limit reached.")
        sys.exit(2)

    config = load_config()
    findings = config.get("last_clarification_findings", {})
    critical = findings.get("critical", 0)
    major = findings.get("major", 0)
    minor = findings.get("minor", 0)
    report_files = _clarification_report_files(clar_dir, start_ts)

    _banner(f"CLARIFICATION EVALUATION RESULT — critical={critical} major={major} minor={minor}")
    if report_files:
        for f in report_files:
            print(f"  {_hyperlink(f)}", flush=True)
    else:
        print(f"  (No new file detected — check the folder manually: {clarifications_path})", flush=True)

    if critical == 0 and major == 0:
        print("[OK] No critical/major findings — clarification is considered DONE.", flush=True)
        if minor:
            print(f"     (Still {minor} minor finding(s) — considered acceptable.)", flush=True)
        print("     Move on to the next stage:  tempa implement  (auto plan runs first)", flush=True)
    elif critical == 0:
        print(f"Only {major} major finding(s) remain (no critical). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Apply the answers to the PRD/spec:                      tempa clarify --apply", flush=True)
        print("  (Or do both at once, evaluate+apply loop:                 tempa clarify --finalize)", flush=True)
    else:
        print(f"[!] There are {critical} critical finding(s). Next steps:", flush=True)
        print("  1. Answer — manually in the file above, or automatically:  tempa clarify --auto-answer", flush=True)
        print("  2. Apply the answers to the PRD/spec:                      tempa clarify --apply", flush=True)
        print("  Then repeat tempa clarify to verify.", flush=True)

    if not noui and report_files:
        saved = run_dashboard(_resolve_prd_dir(config), clar_dir, initial_view="clarification")
        if saved:
            log("Answers saved. Run `tempa clarify --apply` when you're ready to apply them to the PRD/spec.")

    sys.exit(0)


def run_clarify_answer() -> None:
    """Auto-answer — fill in answers for clarification findings that are NOT yet answered
    (one pass). Does not re-evaluate / look for new findings. If every finding already has
    an answer, report that there is nothing left to answer."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    existing = sorted(clar_dir.glob("*.md")) if clar_dir.exists() else []
    if not existing:
        log("No clarification results to answer yet. Run first: tempa clarify")
        sys.exit(0)

    _banner(f"Clarify (auto-answer) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    # Reset the marker so a stale value from a previous run isn't misread.
    config["last_auto_answer"] = 0
    save_config(config)

    start_ts = time.time() - 1
    prompt = build_auto_answer_prompt(config)
    if not run_clarification_session(prompt, 1, get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Auto-answer stopped — Claude usage limit reached.")
            sys.exit(2)
        log("Auto-answer failed.")
        sys.exit(1)
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Auto-answer stopped — Claude usage limit reached.")
        sys.exit(2)

    config = load_config()
    answered = config.get("last_auto_answer", 0)
    changed = _clarification_report_files(clar_dir, start_ts)

    if isinstance(answered, int) and answered > 0:
        print(f"[OK] {answered} clarification finding(s) answered automatically.", flush=True)
        for f in changed:
            print(f"  {_hyperlink(f)}", flush=True)
    else:
        print("[OK] Every clarification finding already has an answer — nothing left to answer.", flush=True)
    sys.exit(0)


def run_clarify_finalize() -> None:
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")

    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    Path(clarifications_path).mkdir(parents=True, exist_ok=True)

    max_run = config.get("max_clarification_run", 10)
    run_number = 0

    _banner(f"Clarify (finalize) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path} | max_runs={max_run}")

    while run_number < max_run:
        run_number += 1

        round_header = f"ROUND {run_number}/{max_run} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        _banner(round_header)
        log(round_header, to_console=False)

        config = load_config()
        prompt = build_clarification_prompt(config)

        success = run_clarification_session(prompt, run_number, get_model(config, "clarify"))
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify (finalize) stopped — Claude usage limit reached.")
            sys.exit(2)
        if not success:
            log(f"Clarification run #{run_number} failed — stopping the loop.")
            sys.exit(1)

        config = load_config()
        findings = config.get("last_clarification_findings", {})
        critical = findings.get("critical", 0)
        major = findings.get("major", 0)
        minor = findings.get("minor", 0)

        log(f"Round #{run_number} findings: critical={critical}, major={major}, minor={minor}")

        if critical == 0 and major == 0:
            log("No critical/major findings. Clarify (finalize) done.")
            if minor > 0:
                log(f"Still {minor} minor finding(s) — considered acceptable.")
            sys.exit(0)

        log(f"Still {critical} critical and {major} major findings remain — applying resolutions to the PRD/spec documents...")

        config = load_config()
        apply_prompt = build_apply_clarification_prompt(config)
        apply_success = run_apply_clarification_session(apply_prompt, run_number, get_model(config, "clarify"))
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Clarify (finalize) stopped — Claude usage limit reached.")
            sys.exit(2)
        if not apply_success:
            log(f"Apply-clarification run #{run_number} failed — stopping the loop.")
            sys.exit(1)

        log(f"Resolutions applied. Running re-evaluation...")

    log(f"Clarify (finalize) reached the {max_run}-run limit. Stopping.")
    sys.exit(1)


def _record_clarify_applied_state(config: dict, clar_dir: Path) -> None:
    """Stamp every current clarification result file's content hash into
    config["clarify_applied_hashes"] right after a successful apply — the dashboard
    compares each file's live hash against this to know whether its currently-recorded
    answers have already been applied to the PRD/spec, or have changed (or never been
    applied) since. Applying doesn't touch the clarification files themselves (only the
    PRD/spec + config), so this is the only record of "applied" state there is."""
    hashes = {}
    for p in _clarification_result_files(clar_dir):
        try:
            hashes[p.name] = hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        except OSError:
            continue
    config["clarify_applied_hashes"] = hashes
    save_config(config)


def _run_apply_step(config: dict) -> bool:
    """Run one apply-clarification session (writes answers/resolutions into the PRD/spec
    documents) and log the outcome. Returns True on success, False on failure. Exits the
    process directly on an auth error or usage-limit hit, matching every other clarify
    subcommand's behavior."""
    prompt = build_apply_clarification_prompt(config)
    if not run_apply_clarification_session(prompt, 1, get_model(config, "clarify")):
        if _state.auth_error_hit:
            sys.exit(3)
        if _state.usage_limit_hit:
            log("Apply stopped — Claude usage limit reached.")
            sys.exit(2)
        log("Apply clarification failed.")
        return False
    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log("Apply stopped — Claude usage limit reached.")
        sys.exit(2)

    config = load_config()
    f = config.get("last_clarification_findings", {})
    log(f"Apply clarification done. Remaining findings: "
        f"critical={f.get('critical', 0)}, major={f.get('major', 0)}, minor={f.get('minor', 0)}")
    _record_clarify_applied_state(config, Path(get_sources(config).get("clarifications", "")))
    return True


def _ask_continue_clarification() -> bool:
    """After an apply step finishes (whether triggered from the web UI or via an explicit
    --apply), ask the user whether to run another clarification round right away. Only
    asked interactively; --finalize already loops by rule and never needs this prompt.
    Returns False (skipping the prompt entirely) when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("Run another clarification round now? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def run_clarify_apply() -> None:
    """Apply the answers/resolutions recorded in the clarification files to the PRD/spec
    documents (one session, WITHOUT re-evaluating). Prerequisite: clarification results
    must already exist — run clarify (and answer, manually or via --auto-answer) first."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)
    clar_dir = Path(clarifications_path)
    existing = _clarification_result_files(clar_dir)
    if not existing:
        log("No clarification results to apply yet. Run first: tempa clarify")
        sys.exit(0)

    _banner(f"Clarify (apply) started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"PRD={sources.get('prd', '?')} | clarifications={clarifications_path}")

    success = _run_apply_step(config)
    if success and _ask_continue_clarification():
        log("Starting another clarification round...")
        run_clarify_once(noui=False)
        return
    sys.exit(0 if success else 1)


def _clarification_result_files(clar_dir: Path) -> list[Path]:
    """All clarification result .md files in `clar_dir`, excluding claude.md (case-
    insensitive), sorted by name for a stable, predictable tab order."""
    if not clar_dir.exists():
        return []
    return sorted(p for p in clar_dir.glob("*.md") if p.name.lower() != "claude.md")


def run_answer_command() -> None:
    """`tempa answer` — open the dashboard's Clarification section, without re-running
    clarify. Scans sources.clarifications for every clarification result file and — if at
    least one has an unanswered finding — opens the dashboard listing all such files in
    the left panel, so nothing unanswered is missed. If every file is already fully
    answered, reports that and does nothing."""
    _init_process_log()

    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    clar_dir = Path(clarifications_path)
    paths = _clarification_result_files(clar_dir)
    if not paths:
        log(f"No clarification files found in {clarifications_path}. Run first: tempa clarify")
        sys.exit(0)

    statuses = [file_answer_status(p) for p in paths]
    unanswered_files = sum(1 for answered, total in statuses if total > 0 and answered < total)
    if unanswered_files == 0:
        log("Every clarification file already has an answer for every finding — nothing left to answer.")
        sys.exit(0)

    log(f"Found {len(paths)} clarification file(s) in {clarifications_path} "
        f"({unanswered_files} with unanswered findings); opening the dashboard.")

    saved = run_dashboard(_resolve_prd_dir(config), clar_dir, initial_view="clarification")
    if saved:
        log("Answers saved. Run `tempa clarify --apply` when you're ready to apply them to the PRD/spec.")
    sys.exit(0)


def _confirm_destructive(cancel_message: str) -> None:
    """Ask for interactive "yes" confirmation before a destructive delete (skippable with
    --yes). Exits the process if not confirmed — never returns in that case."""
    if "--yes" in sys.argv:
        return
    if not sys.stdin.isatty():
        log("Aborted — confirmation required. Run in an interactive terminal, or add --yes.")
        sys.exit(1)
    try:
        answer = input('Type "yes" to confirm the deletion (anything else cancels): ').strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        log(cancel_message)
        sys.exit(0)


def _safety_check_clear_target(dir_path: Path, root: str) -> None:
    """Never delete a drive root, or a folder outside workspace.root."""
    if dir_path == dir_path.parent:
        log(f"ERROR: invalid clear target (drive root): {dir_path}")
        sys.exit(1)
    if root and Path(root).resolve() not in dir_path.resolve().parents and Path(root).resolve() != dir_path.resolve():
        log(f"ERROR: clear target ({dir_path}) is outside workspace.root ({root}). Aborted for safety.")
        sys.exit(1)


def _do_clear_implement() -> tuple[int, int]:
    """Delete all contents of QA_DIR and LOGS_DIR (the harness's own qa/log output — not
    workspace-relative). Returns (qa file count, logs file count) deleted."""
    qa_count = 0
    if QA_DIR.exists():
        qa_count = sum(1 for p in QA_DIR.rglob("*") if p.is_file())
        for child in QA_DIR.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    logs_count = 0
    if LOGS_DIR.exists():
        logs_count = sum(1 for p in LOGS_DIR.rglob("*") if p.is_file())
        for child in LOGS_DIR.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    return qa_count, logs_count


def _do_clear_plan(config: dict, pbi_dir: Path) -> int:
    """Delete all contents of pbi_dir and empty config["epic"] (caller still has to save
    config). Returns the file count deleted."""
    file_count = sum(1 for p in pbi_dir.rglob("*") if p.is_file()) if pbi_dir.exists() else 0
    if pbi_dir.exists():
        for child in pbi_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    config["epic"] = []
    return file_count


def _do_clear_clarify(clar_dir: Path) -> int:
    """Delete everything in clar_dir except a file named claude.md (case-insensitive).
    Returns the item count deleted."""
    keep = {"claude.md"}
    to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []
    for p in to_delete:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    return len(to_delete)


def run_clarify_clear() -> None:
    """Clear clarifications: delete everything in the sources.clarifications folder EXCEPT
    a file named claude.md (case-insensitive). Asks for interactive confirmation; skip with
    --yes. Does not touch config.json."""
    config = load_config()
    sources = get_sources(config)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    clar_dir = Path(clarifications_path)
    _safety_check_clear_target(clar_dir, get_workspace(config).get("root", ""))

    keep = {"claude.md"}  # kept (case-insensitive)
    to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []

    if not to_delete:
        log(f"No clarification files to delete in {clar_dir} (other than claude.md).")
        sys.exit(0)

    file_count = sum(1 for p in to_delete if p.is_file())
    dir_count = sum(1 for p in to_delete if p.is_dir())

    _banner("CLARIFICATION CLEAR — DESTRUCTIVE ACTION")
    print(f"  Folder: {clar_dir} | delete: {file_count} file(s) + {dir_count} folder(s) (permanent) | kept: claude.md", flush=True)
    _confirm_destructive("Clarification clear CANCELLED — nothing was changed.")

    deleted = _do_clear_clarify(clar_dir)
    log(f"Clarification clear done — {deleted} item(s) deleted in {clar_dir} (claude.md kept).")
    sys.exit(0)


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


def _plan_epics_params(config: dict) -> dict:
    """Substitution params for the plan-epics / review prompts (no single epic context)."""
    sources = get_sources(config)
    return {
        "sources.prd": sources.get("prd", ""),
        "sources.docs": sources.get("docs", ""),
        "sources.epics": sources.get("epics", ""),
        "sources.apps": sources.get("apps", ""),
        "config_path": str(CONFIG_PATH),
    }


def build_plan_epics_prompt(config: dict) -> str:
    return build_prompt(load_prompt("plan_epics"), _plan_epics_params(config))


def build_review_epics_prompt(config: dict) -> str:
    return build_prompt(load_prompt("review_epics"), _plan_epics_params(config))


def _plan_epics_run(config: dict) -> bool:
    """Study the PRD → lay out new epics/features/tasks (only what's not yet implemented) →
    write .md files to specs/pbi/epics + append to config.json, then review & fix.

    Called from within implement (not a separate command) — see main(). Returns True if
    generate + review succeed; False on failure (check _state.usage_limit_hit for the cause)."""
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
    if not _run_oneshot_session(gen_prompt, "PLAN-EPICS", "plan_epics_generate", get_model(config, "plan")):
        if not _state.usage_limit_hit and not _state.auth_error_hit:
            log("Generate epic failed — stopping.")
        return False

    # 2) Review & fix
    log("Reviewing & fixing the result (coverage, feature size < 300K, testability, parallelism)...")
    config = load_config()
    review_prompt = build_review_epics_prompt(config)
    if not _run_oneshot_session(review_prompt, "REVIEW-EPICS", "plan_epics_review", get_model(config, "plan")):
        if not _state.usage_limit_hit and not _state.auth_error_hit:
            log("Review epic failed — stopping.")
        return False

    log(f"Plan done. New epic file(s) at: {epics_path}; new epic entries have been added to config.json.")
    return True


def run_plan_clear() -> None:
    """Clear the --plan result: empty the "epic" array in config.json AND delete everything
    in the pbi folder (parent of sources.epics). Asks for interactive confirmation before
    deleting. Skip confirmation with --yes (e.g. for non-interactive use)."""
    config = load_config()
    sources = get_sources(config)
    epics_path = sources.get("epics", "")
    if not epics_path:
        log("ERROR: sources.epics not found in config.json")
        sys.exit(1)

    pbi_dir = Path(epics_path).parent
    _safety_check_clear_target(pbi_dir, get_workspace(config).get("root", ""))

    files = [p for p in pbi_dir.rglob("*") if p.is_file()] if pbi_dir.exists() else []
    epic_count = len((config.get("epic") or []))

    _banner("PLAN CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {pbi_dir} ({len(files)} file(s), all sub-folders) | "
          f"Empty \"epic\" array: config.json ({epic_count} entry(ies) → 0)", flush=True)
    _confirm_destructive("Plan clear CANCELLED — nothing was changed.")

    file_count = _do_clear_plan(config, pbi_dir)
    save_config(config)

    log(f"Plan clear done — contents of {pbi_dir} deleted ({file_count} file(s)), \"epic\" array emptied.")
    sys.exit(0)


def run_implement_clear() -> None:
    """Delete all contents of the harness's own qa/ and logs/ folders (QA reports & session
    logs — not workspace-relative, always SCRIPT_DIR/qa and SCRIPT_DIR/logs). Asks for
    interactive confirmation before deleting. Skip confirmation with --yes."""
    qa_files = [p for p in QA_DIR.rglob("*") if p.is_file()] if QA_DIR.exists() else []
    log_files = [p for p in LOGS_DIR.rglob("*") if p.is_file()] if LOGS_DIR.exists() else []

    if not qa_files and not log_files:
        log(f"Nothing to clear — {QA_DIR} and {LOGS_DIR} are already empty.")
        sys.exit(0)

    _banner("IMPLEMENT CLEAR — DESTRUCTIVE ACTION")
    print(f"  Delete: {QA_DIR} ({len(qa_files)} file(s)) | {LOGS_DIR} ({len(log_files)} file(s))", flush=True)
    _confirm_destructive("Implement clear CANCELLED — nothing was changed.")

    qa_count, logs_count = _do_clear_implement()
    log(f"Implement clear done — {qa_count} file(s) deleted in {QA_DIR}, {logs_count} file(s) deleted in {LOGS_DIR}.")
    sys.exit(0)


def run_clear_all() -> None:
    """Run implement --clear, implement --clear-plan, and clarify --clear together,
    behind a single confirmation prompt. Missing sources.epics/sources.clarifications keys
    still error out (same as the standalone commands); already-empty targets are just skipped."""
    config = load_config()
    sources = get_sources(config)
    root = get_workspace(config).get("root", "")

    epics_path = sources.get("epics", "")
    if not epics_path:
        log("ERROR: sources.epics not found in config.json")
        sys.exit(1)
    clarifications_path = sources.get("clarifications", "")
    if not clarifications_path:
        log("ERROR: sources.clarifications not found in config.json")
        sys.exit(1)

    pbi_dir = Path(epics_path).parent
    clar_dir = Path(clarifications_path)
    _safety_check_clear_target(pbi_dir, root)
    _safety_check_clear_target(clar_dir, root)

    qa_files = [p for p in QA_DIR.rglob("*") if p.is_file()] if QA_DIR.exists() else []
    log_files = [p for p in LOGS_DIR.rglob("*") if p.is_file()] if LOGS_DIR.exists() else []
    plan_files = [p for p in pbi_dir.rglob("*") if p.is_file()] if pbi_dir.exists() else []
    epic_count = len((config.get("epic") or []))
    keep = {"claude.md"}
    clar_to_delete = [c for c in clar_dir.iterdir() if c.name.lower() not in keep] if clar_dir.exists() else []

    if not qa_files and not log_files and not plan_files and epic_count == 0 and not clar_to_delete:
        log("Nothing to clear — qa/, logs/, specs/pbi, and specs/clarifications are already empty.")
        sys.exit(0)

    _banner("CLEAR ALL — DESTRUCTIVE ACTION")
    print(f"  Implement : {QA_DIR} ({len(qa_files)} file(s)) | {LOGS_DIR} ({len(log_files)} file(s))", flush=True)
    print(f"  Plan      : {pbi_dir} ({len(plan_files)} file(s), all sub-folders) | "
          f"empty \"epic\" array: config.json ({epic_count} entry(ies) → 0)", flush=True)
    print(f"  Clarify   : {clar_dir} ({len(clar_to_delete)} item(s), except claude.md)", flush=True)
    _confirm_destructive("Clear CANCELLED — nothing was changed.")

    qa_count, logs_count = _do_clear_implement()
    plan_file_count = _do_clear_plan(config, pbi_dir)
    clarify_count = _do_clear_clarify(clar_dir)
    save_config(config)

    log(f"Clear done — implement: {qa_count} qa file(s) + {logs_count} log file(s) deleted; "
        f"plan: {plan_file_count} file(s) deleted + epic array emptied; "
        f"clarify: {clarify_count} item(s) deleted.")
    sys.exit(0)


def run_verify(epic: str) -> None:
    config = load_config()
    VERIFY_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = VERIFY_DIR / f"{epic}-verify-{timestamp}.md"

    params = _resolve_template_params(config, epic)
    params["output_file"] = str(output_file)

    template = load_prompt("verify")
    prompt = build_prompt(template, params)

    # Extract report content from the final result event as a fallback, in case the
    # session didn't actually write output_file itself.
    result_holder: list[str | None] = [None]

    def _on_json_event(data: dict) -> None:
        if data.get("type") == "result" and data.get("result"):
            result_holder[0] = data["result"]

    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, get_model(config, "implement")),
        log_prefix=f"verify_{epic}",
        banner_label=f"Verification for [{epic}]",
        progress_tag=f"VERIFY {epic}",
        on_json_event=_on_json_event,
    )

    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log(f"Verification stopped — Claude usage limit reached.")
        sys.exit(2)

    if exit_code != 0:
        log(f"Verification FAILED for [{epic}] — exit code {exit_code}")
        _print_log_tail(log_path)
        return

    if output_file.exists():
        log(f"Verification complete — report: {output_file}")
    elif result_holder[0]:
        output_file.write_text(result_holder[0], encoding="utf-8")
        log(f"Verification complete — report saved from response: {output_file}")
    else:
        log(f"Verification finished but no report was generated. Check log: {log_path}")


def print_workspace(config: dict | None = None) -> None:
    """Display the configured working-folder layout with resolved absolute paths."""
    if config is None:
        config = load_config()
    workspace = get_workspace(config)
    resolved = resolve_workspace_paths(config)

    _banner("WORKING FOLDERS")

    root = workspace.get("root", "")
    if not root:
        print("  ⚠ Root folder has not been set. Set it with: tempa set-folders --root <absolute_path>", flush=True)
        return

    for key in DEFAULT_WORKSPACE:
        label = WORKSPACE_LABELS.get(key, key)
        if key == "root":
            print(f"  {label:<26} {workspace[key]}", flush=True)
        else:
            print(f"  {label:<26} {workspace[key]:<12} -> {resolved.get(key, '')}", flush=True)

    if not Path(root).exists():
        print(f"  ⚠ Root folder does not exist on disk yet: {root}", flush=True)


def set_working_folders(args: argparse.Namespace) -> None:
    """Set the default working-folder layout in config.json (key "workspace").

    Usage:
      tempa set-folders --root <absolute_path>
                 [--docs <rel>] [--adr <rel>] [--specs <rel>]
                 [--apps <rel>] [--infra <rel>] [--archive <rel>]

    `--root` MUST be an absolute path. Every other folder is relative to root and
    falls back to its default when omitted (docs, adr, specs, apps, infra, archive).
    """
    config = load_config()
    workspace = get_workspace(config)

    if args.root is not None:
        root_path = Path(args.root)
        if not root_path.is_absolute():
            log(f"ERROR: --root must be an absolute path, not '{args.root}'")
            sys.exit(1)
        workspace["root"] = str(root_path)

    for flag, key in (
        ("--docs", "docs"),
        ("--adr", "adr"),
        ("--specs", "specs"),
        ("--apps", "apps"),
        ("--infra", "infra"),
        ("--archive", "archive"),
    ):
        value = getattr(args, key)
        if value is not None:
            if Path(value).is_absolute():
                log(f"ERROR: {flag} must be a path relative to root, not an absolute path '{value}'")
                sys.exit(1)
            workspace[key] = value

    if not workspace.get("root"):
        log("ERROR: root folder must be set (absolute path). "
            "Example: tempa set-folders --root C:\\work\\repo\\qlar-medical-clinic-backoffice")
        sys.exit(1)

    config["workspace"] = workspace
    save_config(config)

    log("Working folders saved to config.json (key \"workspace\").")
    print_workspace(config)


def run_init(args: argparse.Namespace) -> None:
    """Initialize working folders: set workspace.root in config.json, then create the
    default working folders on disk (docs, adr, specs, apps, infra, archive) under root,
    plus every configured `sources` folder (prd, epics, clarifications, ...) so the
    expected structure (e.g. specs/prd) exists upfront instead of only appearing once
    clarify/implement first write to it.

    Usage:
      tempa init <absolute_path>

    Folders that already exist on disk are NOT recreated and their contents are NEVER
    overwritten — only folders that don't exist yet are created.
    """
    root = args.root
    if root is None:
        log("ERROR: init requires a root folder path (absolute). "
            "Example: tempa init C:\\work\\repo\\qlar-medical-clinic-backoffice")
        sys.exit(1)

    root_path = Path(root)
    if not root_path.is_absolute():
        log(f"ERROR: root must be an absolute path, not '{root}'")
        sys.exit(1)

    config = load_config()
    workspace = get_workspace(config)
    workspace["root"] = str(root_path)
    config["workspace"] = workspace
    save_config(config)
    log("Working folders saved to config.json (key \"workspace\").")

    # Create the root folder first (if missing), then every sub-folder under it.
    # exist_ok=True makes this operation idempotent: folders that already exist
    # are not recreated and their contents are never touched/overwritten.
    if root_path.exists():
        log(f"Root folder already exists, skipping: {root_path}")
    else:
        root_path.mkdir(parents=True, exist_ok=True)
        log(f"Root folder created: {root_path}")

    resolved = resolve_workspace_paths(config)
    for key in DEFAULT_WORKSPACE:
        if key == "root":
            continue
        folder = Path(resolved[key])
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Also create every configured `sources` folder (e.g. specs/prd, specs/pbi/epics,
    # specs/clarifications) so the expected input/output structure exists from the start,
    # not just the ones clarify/implement happen to create lazily on first write.
    for key, path_str in get_sources(config).items():
        if not path_str:
            continue
        folder = Path(path_str)
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Ensure the specs/ folder (working specifications — not meant to be version
    # controlled) is git-ignored: create .gitignore if missing, append the entry if absent.
    gitignore_path = root_path / ".gitignore"
    specs_entry = f"{workspace['specs']}/"
    if not gitignore_path.exists():
        with open(gitignore_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(specs_entry + "\n")
        log(f".gitignore created: {gitignore_path}")
    else:
        existing_text = gitignore_path.read_text(encoding="utf-8")
        existing_lines = existing_text.splitlines()
        if specs_entry in existing_lines or workspace["specs"] in existing_lines:
            log(f".gitignore already ignores '{specs_entry}', skipping: {gitignore_path}")
        else:
            with open(gitignore_path, "a", encoding="utf-8", newline="\n") as f:
                if existing_text and not existing_text.endswith("\n"):
                    f.write("\n")
                f.write(specs_entry + "\n")
            log(f"Added '{specs_entry}' to .gitignore: {gitignore_path}")

    print_workspace(config)


def _resolve_model_alias(value: str) -> str:
    """Map a friendly alias (e.g. "opus-4.8") to its full model id. If `value` is not
    a known alias, return it unchanged (assumed to already be a valid model id)."""
    return MODEL_ALIASES.get(value.strip().lower(), value)


def get_models(config: dict) -> dict:
    """Return the per-stage models merged over DEFAULT_MODELS (missing stage → default)."""
    models = dict(DEFAULT_MODELS)
    models.update(config.get("models", {}))
    return models


def get_model(config: dict, stage: str) -> str:
    """Return the model id configured for a stage (clarify | plan | implement)."""
    return get_models(config).get(stage, DEFAULT_MODELS.get(stage, "claude-sonnet-5"))


def print_models(config: dict | None = None) -> None:
    """Display the AI model configured for each harness stage."""
    if config is None:
        config = load_config()
    models = get_models(config)
    _banner("AI MODEL PER STAGE")
    labels = {
        "clarify": "Clarify   (clarify)",
        "plan": "Plan      (plan)",
        "implement": "Implement (implement, QA, verify)",
    }
    for stage in ("clarify", "plan", "implement"):
        print(f"  {labels[stage]:<34} {models.get(stage, '?')}", flush=True)


def set_models(args: argparse.Namespace) -> None:
    """Set the AI model per stage in config.json (key "models").

    Usage:
      tempa set-model [--clarify <model>] [--plan <model>] [--implement <model>]

    <model> accepts a friendly alias (opus-4.8, sonnet-5, haiku-4.5, fable-5) or a full
    model id (e.g. claude-opus-4-8). Stages omitted keep their current/default value.
    """
    config = load_config()
    models = get_models(config)

    changed = False
    for stage in ("clarify", "plan", "implement"):
        value = getattr(args, stage)
        if value is not None:
            models[stage] = _resolve_model_alias(value)
            changed = True

    config["models"] = models
    save_config(config)
    if changed:
        log("AI model saved to config.json (key \"models\").")
    else:
        log("No model flag given (--clarify/--plan/--implement) — showing the current configuration.")
    print_models(config)


def print_help() -> None:
    config = load_config()
    sessions = (config.get("epic") or [])
    total = len(sessions)
    done = sum(1 for s in sessions if s["status"] == "done")
    on_progress = next((s for s in sessions if s["status"] == "on_progress"), None)
    failed = [s for s in sessions if s["status"] == "failed"]
    pending = sum(1 for s in sessions if s["status"] == "pending")

    print(f"""
Agent Runner — Qlar Medical Clinic Back-Office
===============================================

Config  : {CONFIG_PATH}
Work dir: {WORKING_DIR}
Poll    : {POLL_INTERVAL_SEC}s

USAGE

  -- Configuration --
  tempa init <abs>            Init project: set workspace.root + CREATE working folders on disk
                                  (existing folders are skipped, their contents are never overwritten)
  tempa set-folders --root <abs> [--docs r] [--adr r] [--specs r] [--apps r] [--infra r] [--archive r]
                                  Only set the default working folders (without creating them on disk)
  tempa show-folders         Show the active working folders (+ resolved absolute paths)
  tempa set-model [--clarify m] [--plan m] [--implement m]
                                  Set the AI model per stage (alias: opus-4.8, sonnet-5, ...)
  tempa show-models          Show the AI model per stage
  tempa test                 Permission test (verifies the claude CLI runs)
  tempa --help               Show this help

  -- Create Spec & Clarification --
  tempa clarify              Evaluate PRD clarification once (manual): write findings to file, show counts + file path,
                                  then open the dashboard on the Clarification section (add --noui to skip it)
  tempa clarify --noui       Same as above, but skip opening the dashboard
  tempa answer               Scan sources.clarifications for clarification files, and — if any finding is
                                  still unanswered — open the dashboard's Clarification section listing every
                                  such file in the left panel, without re-running clarify
  tempa clarify --auto-answer  Automatically answer unanswered clarification findings (without re-evaluating)
  tempa clarify --apply      Apply answers from the clarification files to the PRD/spec documents (without re-evaluating)
  tempa clarify --finalize   Automatic PRD clarification loop (evaluate + answer until no critical/major remain)
  tempa clarify --clear      Delete all files in specs/clarifications except claude.md (asks for confirmation; --yes to skip)

  -- Plan & Start Implementation --
  tempa implement            Start the agent runner (polls every {POLL_INTERVAL_SEC}s).
                                  If there's no task (epic/feature/QA) in config.json yet, run
                                  plan (lay out Epic/Feature/Task from the PRD) automatically first.
  tempa implement --replan   Force re-running plan first, then continue/start implementation
  tempa implement --features 4  Start with a limit of 4 features per session (overrides config)
  tempa implement --clear-plan  Clear plan: delete ALL contents of the specs/pbi folder + empty the "epic" array (asks for confirmation; --yes to skip)
                                  (plan generation itself is now part of implement, see above)
  tempa implement --reset         Reset on_progress → pending (clears session_id)
  tempa implement --reset-failed  Reset all failed → pending
  tempa implement --reset-qa      Reset qa_passed=false for all done epics (forces QA to re-run)
  tempa implement --clear    Delete ALL files in the qa/ and logs/ folders (asks for confirmation; --yes to skip)

  -- Monitoring & Utilities --
  tempa dashboard            Open the web dashboard (Home / Specification / Clarification / Implementation
                                  in a Windows-Explorer-style left panel, content on the right; Ctrl+C to stop)
  tempa spec --show          Open the dashboard directly on the Specification section: browse the PRD
                                  file/subfolder tree, view or edit any markdown file, and save changes back to disk
  tempa verify <epic>        Verify whether the epic specification has been implemented
  tempa clear                Run implement --clear + implement --clear-plan + clarify --clear together,
                                  behind a single confirmation (asks for confirmation; --yes to skip)
  tempa status               Show a progress summary of all sessions

GLOBAL FLAGS
  --show-prompt                   Show the prompt sent to Claude on the console (default: off; the prompt is always recorded to the log). Applies to every command that runs a session — pass it AFTER the subcommand, e.g. `tempa implement --show-prompt`.

CONFIG OPTIONS (config.json)
  features_per_session            Max features per session (null = no limit)
  workspace.root                  Root folder (MUST be absolute) — every other folder is relative to this
  workspace.docs                  Current application documentation folder (default: docs)
  workspace.adr                   Architecture decision record folder (default: adr)
  workspace.specs                 NEW specification folder to be worked on (default: specs)
  workspace.apps                  Application implementation folder (default: apps)
  workspace.infra                 Infrastructure scripts folder, e.g. docker compose (default: infra)
  workspace.archive                Archive folder for old, unused specifications (default: archive)
  sources.*                       RELATIVE to workspace.root (absolute paths are still supported)
  sources.prd                     PRD folder = the NEW specification to be worked on
  sources.docs                    CURRENT system documentation folder (reference for 'what already exists')
  sources.epics                   Path to the epic spec folder (plan output, run via implement)
  sources.apps                    Monorepo root (ALL services; each service's src & tests live inside it)
  models.clarify                  AI model for clarify (default: claude-opus-4-8)
  models.plan                     AI model for the plan stage, run via implement (default: claude-sonnet-5)
  models.implement                AI model for implement/QA/verify (default: claude-sonnet-5)

PROMPT TEMPLATES (prompt/ folder, one .md file per prompt — no longer in config.json)
  prompt/implementation.md        New implementation prompt
  prompt/continuation.md          Prompt for resuming a session (fallback: implementation.md)
  prompt/verify.md                Implementation verification prompt (verify)
  prompt/qa.md                    Automatic QA prompt (run after an epic is done)
  prompt/qa_continuation.md       Prompt for resuming QA (fallback: qa.md)
  prompt/clarification.md         PRD clarification evaluation prompt (clarify)
  prompt/auto_answer.md           Prompt for answering unanswered findings (clarify --auto-answer)
  prompt/apply_clarification.md   Prompt for applying clarification resolutions (clarify --finalize)
  prompt/plan_epics.md            Prompt to generate new epics/features/tasks (plan, run via implement)
  prompt/review_epics.md          Prompt to review & fix the result (plan, run via implement)
  Available placeholders: ${{epic}}, ${{sources}}, ${{sources.<key>}}, ${{config_path}},
  ${{output_file}}, ${{qa_output_file}} (depends on the prompt).

SESSION STATUS
  pending        Not started yet
  on_progress    Currently running
  done           Done (set by Claude)
  require_fixing Already implemented but has QA findings — will be fixed automatically
  failed         Error — fix it then run implement --reset-failed

QA STATUS (qa_passed field per epic)
  false (🔍)   QA has not run yet — the runner will run QA before the next implementation
  true  (✅)   QA has passed — the next epic's implementation may start
  QA reports are saved at: {SCRIPT_DIR / "qa"}

PROGRESS ({done}/{total} epics done)""")

    if on_progress:
        label = on_progress.get("epic_name", "?")
        total_f = on_progress.get("total_features", 0)
        completed_f = on_progress.get("completed_features", 0)
        progress_str = f"{completed_f}/{total_f}" if total_f else "?"
        sid = on_progress.get("claude_session_id", "-")
        print(f"  IN PROGRESS : {label} ({progress_str} features) — session_id: {sid}")
    if failed:
        for s in failed:
            print(f"  FAILED      : {s.get('epic_name', '?')}")
    print(f"  Pending     : {pending} session(s)")
    print()


def print_status() -> None:
    config = load_config()
    sessions = (config.get("epic") or [])
    _banner("SESSION STATUS")
    for s in sessions:
        epic = s.get("epic_name", "?")
        status = s["status"]
        total_f = s.get("total_features", 0)
        completed_f = s.get("completed_features", 0)
        last_run = s.get("last_run", "")[:16].replace("T", " ") if s.get("last_run") else "-"

        status_icons = {"done": "✅", "on_progress": "🔄", "pending": "⬜", "failed": "❌", "require_fixing": "🔧"}
        icon = status_icons.get(status, "?")
        qa_tag = ""
        if status == "done":
            qa_tag = "  [QA ok]" if s.get("qa_passed", False) else "  [QA --]"
        print(f"{icon} {epic:<10} {status:<16} {completed_f}/{total_f} features   last run: {last_run}{qa_tag}")

        feat_icons = {"done": "✅", "failed": "❌", "require_fixing": "🔧"}
        for feat in s.get("features", []):
            feat_icon = feat_icons.get(feat["status"], "⬜")
            print(f"   {feat_icon} {feat['id']} — {feat['name']}")


def _resolve_clar_dir(config: dict) -> Path:
    """Return the absolute path of the clarifications folder (sources.clarifications),
    falling back to <specs>/clarifications when it isn't configured."""
    clar_dir_str = get_sources(config).get("clarifications", "")
    return Path(clar_dir_str) if clar_dir_str else resolve_specs_dir(config) / "clarifications"


def run_spec_show() -> None:
    """`tempa spec --show` — open the dashboard directly on the Specification section:
    a tree of PRD files/subfolders on the left, a markdown view/edit pane on the right.
    Blocks until the user stops the server (Ctrl+C)."""
    config = load_config()
    prd_dir = _resolve_prd_dir(config)
    if not prd_dir.exists():
        log(f"PRD folder not found: {prd_dir}")
        log("Create it (or point sources.prd at the right folder in config.json / "
            "'tempa init <root>') and add specification files first.")
        sys.exit(1)
    if not prd_dir.is_dir():
        log(f"PRD path is not a folder: {prd_dir}")
        sys.exit(1)
    run_dashboard(prd_dir, _resolve_clar_dir(config), initial_view="specification")


def run_dashboard_command() -> None:
    """`tempa dashboard` — open the web dashboard on the Home view."""
    config = load_config()
    run_dashboard(_resolve_prd_dir(config), _resolve_clar_dir(config), initial_view="home")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the subcommand parser. `-h`/`--help` is intentionally NOT registered here —
    see __main__, which checks for it in raw sys.argv before parsing at all, so it always
    shows the same rich hand-written help (print_help()) regardless of what else is on the
    command line, the same way it did before this was converted to argparse."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--show-prompt", action="store_true", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="tempa.py", add_help=False)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", parents=[common], add_help=False)
    p.add_argument("root", nargs="?", default=None)

    p = sub.add_parser("set-folders", parents=[common], add_help=False)
    for flag in ("--root", "--docs", "--adr", "--specs", "--apps", "--infra", "--archive"):
        p.add_argument(flag)

    sub.add_parser("show-folders", parents=[common], add_help=False)

    p = sub.add_parser("set-model", parents=[common], add_help=False)
    p.add_argument("--clarify")
    p.add_argument("--plan")
    p.add_argument("--implement")

    sub.add_parser("show-models", parents=[common], add_help=False)
    sub.add_parser("test", parents=[common], add_help=False)
    sub.add_parser("status", parents=[common], add_help=False)

    sub.add_parser("dashboard", parents=[common], add_help=False)

    p = sub.add_parser("spec", parents=[common], add_help=False)
    p.add_argument("--show", action="store_true")

    p = sub.add_parser("clarify", parents=[common], add_help=False)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--finalize", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--auto-answer", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--noui", action="store_true")

    sub.add_parser("answer", parents=[common], add_help=False)

    # Deprecated: plan generation is now folded into `implement`. Kept as a subcommand
    # purely to redirect anyone who still types it out of habit.
    sub.add_parser("plan", parents=[common], add_help=False)

    p = sub.add_parser("verify", parents=[common], add_help=False)
    p.add_argument("epic")

    p = sub.add_parser("implement", parents=[common], add_help=False)
    p.add_argument("--reset-failed", action="store_true")
    p.add_argument("--reset-qa", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--clear-plan", action="store_true")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--features")
    p.add_argument("--replan", action="store_true")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("clear", parents=[common], add_help=False)
    p.add_argument("--yes", action="store_true")

    return parser


def _reset_failed_epics() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "failed":
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["status"] = "pending"
            config["epic"][i].pop("claude_session_id", None)
            reset_count += 1
            log(f"Reset [{label}] → pending")
    if reset_count == 0:
        log("No failed sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {reset_count} failed session(s). Ready to restart.")


def _reset_qa_state() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "done" and (session.get("qa_passed", False) or session.get("qa_status") in ("ongoing", "done")):
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["qa_passed"] = False
            config["epic"][i]["qa_status"] = "idle"
            config["epic"][i]["qa_session_id"] = ""
            config["epic"][i]["qa_total_run"] = 0
            config["epic"][i]["qa_report_filename"] = ""
            reset_count += 1
            log(f"Reset QA [{label}] → qa_passed=false, qa_status=idle")
    if reset_count == 0:
        log("No done epics with QA state found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset QA for {reset_count} epic(s). QA will be re-run.")


def _reset_on_progress_epics() -> None:
    config = load_config()
    reset_count = 0
    for i, session in enumerate(config.get("epic") or []):
        if session["status"] == "on_progress":
            label = session.get("epic_name", f"epic_{i}")
            config["epic"][i]["status"] = "pending"
            config["epic"][i].pop("claude_session_id", None)
            reset_count += 1
            log(f"Reset [{label}] → pending (session_id cleared)")
    if reset_count == 0:
        log("No on_progress sessions found — nothing to reset")
    else:
        save_config(config)
        log(f"Reset {reset_count} session(s). Ready to restart.")


def _dispatch_clarify(args: argparse.Namespace) -> None:
    if args.clear:
        run_clarify_clear()
    elif args.finalize:
        run_clarify_finalize()
    elif args.apply:
        run_clarify_apply()
    elif args.auto_answer:
        run_clarify_answer()
    else:
        run_clarify_once(noui=args.noui)


def _dispatch_implement(args: argparse.Namespace) -> None:
    if args.reset_failed:
        _reset_failed_epics()
    elif args.reset_qa:
        _reset_qa_state()
    elif args.reset:
        _reset_on_progress_epics()
    elif args.clear_plan:
        run_plan_clear()
    elif args.clear:
        run_implement_clear()
    else:
        features_override = None
        if args.features is not None:
            try:
                features_override = int(args.features)
            except ValueError:
                print(f"--features must be a number, not '{args.features}'")
                sys.exit(1)
        main(features_override=features_override, replan=args.replan)


if __name__ == "__main__":
    # Checked in raw argv, before argparse ever runs, so --help always shows the same
    # hand-written help regardless of what other (possibly invalid) flags are present.
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)

    cli_args = _build_arg_parser().parse_args()

    if cli_args.command is None:
        print("No command given. Run 'tempa --help' for usage.")
        sys.exit(1)
    elif cli_args.command == "status":
        print_status()
    elif cli_args.command == "init":
        run_init(cli_args)
    elif cli_args.command == "set-folders":
        set_working_folders(cli_args)
    elif cli_args.command == "show-folders":
        print_workspace()
    elif cli_args.command == "set-model":
        set_models(cli_args)
    elif cli_args.command == "show-models":
        print_models()
    elif cli_args.command == "test":
        run_test()
    elif cli_args.command == "dashboard":
        run_dashboard_command()
    elif cli_args.command == "spec":
        if not cli_args.show:
            print("Usage: tempa spec --show")
            sys.exit(1)
        run_spec_show()
    elif cli_args.command == "clarify":
        _dispatch_clarify(cli_args)
    elif cli_args.command == "answer":
        run_answer_command()
    elif cli_args.command == "plan":
        print("Plan is now run automatically by 'tempa implement' (when there's no "
              "epic/feature/QA task yet), or force it with 'tempa implement --replan'.")
        print("To clear a previous plan result: tempa implement --clear-plan")
        sys.exit(1)
    elif cli_args.command == "verify":
        run_verify(cli_args.epic)
    elif cli_args.command == "implement":
        _dispatch_implement(cli_args)
    elif cli_args.command == "clear":
        run_clear_all()
