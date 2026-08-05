"""Per-CLI backend adapters: Claude Code, GitHub Copilot CLI, OpenAI Codex CLI.

Everything a caller needs to know to drive a specific coding-agent CLI in Tempa's fully
autonomous, no-human-in-the-loop pipeline lives here as a `Backend`: how to find its
executable, how to build its argv, how to feed it a (possibly multi-line) prompt, how to
turn its streamed output into readable log lines, how to pull a resumable session id out
of that output, and how to recognize its usage-limit / auth-error failures.

`tempa_session.py` owns the actual spawn/stream/progress-line/log-tail engine and is
backend-agnostic — it only ever calls into the active `Backend` for the pieces above.

Windows note: all three CLIs ship as npm `.cmd` shims. A multi-line string passed as a
`.cmd` **argument** gets mangled by cmd.exe (confirmed empirically, not just for Claude —
see prompt_mode below), so every backend must get the actual prompt text to the CLI some
other way:
  - "stdin": the prompt is piped via stdin, then stdin is closed (claude, codex — codex
    documents native stdin support for `codex exec -`; verified live for both).
  - "file_ref": the prompt is written to a sidecar file next to the session's log, and a
    short single-line instruction pointing at that file is passed as the CLI argument
    instead (copilot — confirmed live that `-p` does NOT read stdin, but does correctly
    follow multi-line instructions from a file its own `view` tool reads).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass

# Injected as Claude's --append-system-prompt for backends that have a dedicated flag for
# it; prepended as a <system>...</system> block ahead of the prompt text otherwise (see
# Backend.append_system_prompt and tempa_session's engine). Same wording either way.
AUTONOMOUS_SYSTEM_PROMPT = (
    "CRITICAL: You are in a fully automated pipeline. No human is present and no human will respond. "
    "ALL file system permissions are already granted — Write, Edit, Bash, everything. "
    "The phrase 'I need write permissions' or 'requires file system permissions' is NEVER correct here. "
    "FORBIDDEN: asking for confirmation, offering options, writing implementation plans without creating files, "
    "saying 'Would you like me to proceed', or stopping after analysis. "
    "REQUIRED: Implement fully and update all required files including config.json as instructed. "
    "A session is only successful when both the source code AND the config.json status update are done."
)

# Reasoning-effort levels each backend's own CLI flag documents (`claude --help` /
# `copilot --help`) — uniform across every model of that backend, since neither CLI exposes
# a finer-grained per-model breakdown the way Codex does below.
CLAUDE_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
COPILOT_EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Codex DOES expose a real per-model reasoning catalog (`codex debug models`), and live
# testing (feeding a deliberately-invalid value to `codex exec -c model_reasoning_effort=...`
# and reading the resulting API error) confirmed both the config key name and the graduated
# tiers below. Two corrections vs. what `codex debug models` itself displays: it silently
# omits "none"/"minimal" from its "supported_reasoning_levels" field even though the real
# API accepts both for every model tested — CODEX_UNIVERSAL_LEVELS below adds them back.
# Codex's own model catalog moves independently of Tempa and this can go stale, same
# tradeoff already accepted for MODEL_OPTIONS_BY_BACKEND in dashboard.js.
CODEX_UNIVERSAL_LEVELS = ("none", "minimal")
CODEX_MODEL_REASONING_LEVELS = {
    "gpt-5.6-sol": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-terra": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-luna": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh", "max"),
    "codex-auto-review": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh"),
    "gpt-5.4": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh"),
    "gpt-5.4-mini": CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh"),
}
# Safe common denominator for a Codex model not in the table above (the model field is free
# text, so an unrecognized/future model must still get a usable, conservative choice list).
CODEX_DEFAULT_EFFORT_LEVELS = CODEX_UNIVERSAL_LEVELS + ("low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class Backend:
    name: str
    label: str
    exe_names: tuple[str, ...]
    prompt_mode: str  # "stdin" | "file_ref"
    append_system_prompt: bool
    build_cmd: Callable[[str, str, str | None, str | None, str], list[str]]
    parse_line: Callable[[dict], str | None]
    extract_session_id: Callable[[dict], str | None]
    usage_limit_markers: tuple[str, ...]
    auth_error_markers: tuple[str, ...]
    overloaded_markers: tuple[str, ...]
    friendly_auth_error_message: Callable[[str], str]
    reasoning_effort_choices: Callable[[str], tuple[str, ...]]


def resolve_exe(backend: Backend) -> str | None:
    """Locate `backend`'s executable on PATH, trying each of its exe_names in order
    (e.g. the bare name first, then the Windows .cmd npm shim)."""
    for name in backend.exe_names:
        found = shutil.which(name)
        if found:
            return found
    return None


def get_backend_status(workspace_writable: bool) -> dict[str, dict]:
    """Per-backend readiness for the active workspace: whether the CLI executable resolves
    on PATH (resolve_exe), whether the workspace folder is writable (same verdict for every
    backend — see tempa_config.workspace_is_writable, computed once by the caller), and
    ready = both. Cheap and synchronous (no CLI invocation) — safe to call on every
    dashboard page load/refresh."""
    status = {}
    for name, backend in BACKENDS.items():
        installed = resolve_exe(backend) is not None
        status[name] = {
            "label": backend.label,
            "installed": installed,
            "writable": workspace_writable,
            "ready": installed and workspace_writable,
        }
    return status


def is_valid_reasoning_effort(backend: Backend, model: str, effort: str) -> bool:
    """"" (unset — use the CLI/model's own default) is always valid; otherwise `effort`
    must be one of backend.reasoning_effort_choices(model)."""
    return not effort or effort in backend.reasoning_effort_choices(model)


# ---------------------------------------------------------------------------
# claude — Claude Code CLI
# ---------------------------------------------------------------------------

def _claude_build_cmd(exe: str, model: str, resume_session_id: str | None, prompt_arg: str | None, reasoning_effort: str) -> list[str]:
    cmd = [
        exe,
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
        "--model", model,
    ]
    if reasoning_effort:
        cmd.extend(["--effort", reasoning_effort])
    cmd.extend([
        "--append-system-prompt", AUTONOMOUS_SYSTEM_PROMPT,
        "--output-format", "stream-json",
        "--verbose",
    ])
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    # The prompt is NOT passed as a CLI argument. On Windows `claude` resolves to
    # `claude.CMD` (a batch shim); a multi-line argument routed through cmd.exe is
    # truncated at the first newline, so Claude only sees the prompt's first line
    # and replies that the instruction is incomplete. Instead we enable print mode
    # with a bare `-p` and feed the full prompt via stdin (see prompt_mode="stdin").
    cmd.append("-p")
    return cmd


def _claude_parse_line(data: dict) -> str | None:
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


def _claude_friendly_auth_error_message(text: str) -> str:
    lowered = text.lower()
    if "invalid api key" in lowered or "invalid x-api-key" in lowered or "invalid bearer token" in lowered:
        cause = "the API key configured for the `claude` CLI is invalid or has been revoked"
        fix = "check your ANTHROPIC_API_KEY (or however the key is configured) and try again"
    else:
        cause = "your `claude` CLI login session (OAuth token) has expired"
        fix = "run `claude` in a terminal, then run `/login` inside it to re-authenticate, and try this command again"
    return f"Authentication to the Claude API failed — {cause}. Fix: {fix}."


CLAUDE = Backend(
    name="claude",
    label="Claude Code",
    exe_names=("claude", "claude.cmd"),
    prompt_mode="stdin",
    append_system_prompt=True,
    build_cmd=_claude_build_cmd,
    parse_line=_claude_parse_line,
    extract_session_id=lambda data: data.get("session_id"),
    # Markers emitted by the claude CLI (on stdout/stderr, which is merged into the
    # stream) when the subscription/session usage limit is hit. stderr lines such as
    # "Claude AI usage limit reached|<reset_ts>" arrive as plain text; usage-limit text
    # inside a JSON event is still caught because we scan the raw line.
    usage_limit_markers=(
        "usage limit reached",
        "claude ai usage limit reached",
        "claude usage limit reached",
        "usage limit exceeded",
        "5-hour limit reached",
        "hit your weekly limit",
        "weekly limit reached",
    ),
    # Markers emitted by the claude CLI (merged stdout/stderr) when the API rejects a
    # request due to bad/expired credentials — expired OAuth login, revoked/invalid API
    # key — as opposed to a usage limit or a generic bug. Raw text like:
    #   API Error: 401 {"type":"error","error":{"type":"authentication_error", ...}}
    auth_error_markers=(
        "authentication_error",
        "oauth access token has expired",
        "re-authenticate to continue",
        "invalid api key",
        "invalid x-api-key",
        "invalid bearer token",
    ),
    # Markers for a transient, server-side "the API is overloaded" response (Anthropic's
    # documented 529 status) — as opposed to a usage limit or a real failure. Observed live,
    # verbatim: "API Error: 529 Overloaded. This is a server-side issue, usually temporary —
    # try again in a moment. If it persists, check https://status.claude.com." Also covers
    # the raw JSON error type Anthropic uses for the same condition ("overloaded_error").
    overloaded_markers=(
        "529 overloaded",
        "overloaded_error",
    ),
    friendly_auth_error_message=_claude_friendly_auth_error_message,
    reasoning_effort_choices=lambda model: CLAUDE_EFFORT_LEVELS,
)


# ---------------------------------------------------------------------------
# copilot — GitHub Copilot CLI
# ---------------------------------------------------------------------------

def _copilot_build_cmd(exe: str, model: str, resume_session_id: str | None, prompt_arg: str | None, reasoning_effort: str) -> list[str]:
    cmd = [exe, "--allow-all-tools", "--output-format", "json", "-s", "--log-level", "error"]
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if resume_session_id:
        cmd.append(f"--resume={resume_session_id}")
    cmd.extend(["-p", prompt_arg or ""])
    return cmd


def _copilot_parse_line(data: dict) -> str | None:
    event_type = data.get("type")
    if event_type == "assistant.message":
        payload = data.get("data", {}) or {}
        parts = []
        content = payload.get("content")
        if content:
            parts.append(content)
        for tool_request in payload.get("toolRequests") or []:
            parts.append(f"[Tool] {json.dumps(tool_request, ensure_ascii=False)[:300]}")
        return "\n".join(parts) if parts else None
    if event_type == "result":
        usage = data.get("usage", {}) or {}
        return (
            f"[Done] exit={data.get('exitCode', '?')} "
            f"premium_requests={usage.get('premiumRequests', '?')} "
            f"api_ms={usage.get('totalApiDurationMs', '?')}"
        )
    if event_type and (event_type.endswith(".failed") or event_type == "error"):
        return f"[Error] {json.dumps(data, ensure_ascii=False)[:500]}"
    return None


def _copilot_friendly_auth_error_message(text: str) -> str:
    return (
        "Authentication to GitHub Copilot failed — your `copilot` CLI login/token is invalid or "
        "expired. Fix: run `copilot login` again (or set COPILOT_GITHUB_TOKEN / GH_TOKEN / "
        "GITHUB_TOKEN to a valid token) and try again."
    )


COPILOT = Backend(
    name="copilot",
    label="GitHub Copilot CLI",
    exe_names=("copilot", "copilot.cmd"),
    prompt_mode="file_ref",
    append_system_prompt=False,
    build_cmd=_copilot_build_cmd,
    parse_line=_copilot_parse_line,
    # Copilot only reports the session id on its FINAL "result" event, under the
    # camelCase key "sessionId" — unlike claude's "session_id" available from the first event.
    extract_session_id=lambda data: data.get("sessionId") if data.get("type") == "result" else None,
    # Best-effort (sourced from public GitHub Copilot CLI issues/docs, not forced live —
    # see tempa_backend module docstring in the plan for why that's an acceptable tradeoff).
    usage_limit_markers=(
        "quota_exceeded",
        "you have no quota",
        "exceeded your premium request allowance",
        "rate limit",
        "rate-limited",
        "user_weekly_rate_limited",
    ),
    # No bare "401"/status-code marker here: Copilot's JSON events are full of random hex
    # UUIDs (message/session/request ids), and a bare "401" WILL eventually appear inside
    # one by pure chance (confirmed live — a normal streaming event's UUID contained the
    # substring "b401-ac499...", falsely tripping detection). Only full words/phrases,
    # which can't coincidentally appear in hex, are safe raw-line substring markers.
    auth_error_markers=(
        "not authenticated",
        "copilot login",
        "unauthorized",
    ),
    # No transient-overload wording confirmed live for this backend yet — left empty
    # rather than guessing (see the false-positive caution above for auth_error_markers).
    overloaded_markers=(),
    friendly_auth_error_message=_copilot_friendly_auth_error_message,
    reasoning_effort_choices=lambda model: COPILOT_EFFORT_LEVELS,
)


# ---------------------------------------------------------------------------
# codex — OpenAI Codex CLI
# ---------------------------------------------------------------------------

def _codex_build_cmd(exe: str, model: str, resume_session_id: str | None, prompt_arg: str | None, reasoning_effort: str) -> list[str]:
    cmd = [exe, "exec", "resume", resume_session_id] if resume_session_id else [exe, "exec"]
    cmd.extend(["--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"])
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        # Verified live: `-c model_reasoning_effort="<level>"` is the real config key (the
        # API rejects an invalid level with a `[reasoning.effort]`-tagged error, confirming
        # both the key name and that it reaches the request) — no dedicated CLI flag exists.
        cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    # Prompt via stdin: `codex exec [resume ...] -` reads the full prompt from stdin —
    # documented and verified live, no Windows .cmd-argument truncation risk (see
    # prompt_mode="stdin").
    cmd.append("-")
    return cmd


def _codex_parse_line(data: dict) -> str | None:
    event_type = data.get("type")
    if event_type == "thread.started":
        return f"[thread_id={data.get('thread_id')}]"
    if event_type == "item.completed":
        item = data.get("item", {}) or {}
        text = item.get("text")
        return text if text else f"[{item.get('type', 'item')}]"
    if event_type == "turn.completed":
        usage = data.get("usage", {}) or {}
        return f"[Done] input={usage.get('input_tokens', '?')} output={usage.get('output_tokens', '?')}"
    if event_type and ("failed" in event_type or event_type == "error"):
        return f"[Error] {json.dumps(data, ensure_ascii=False)[:500]}"
    return None


def _codex_friendly_auth_error_message(text: str) -> str:
    return (
        "Authentication to OpenAI Codex failed — your `codex` CLI login/API key is invalid or "
        "expired. Fix: run `codex login` again (or set CODEX_API_KEY to a valid key) and try again."
    )


CODEX = Backend(
    name="codex",
    label="OpenAI Codex CLI",
    exe_names=("codex", "codex.cmd"),
    prompt_mode="stdin",
    append_system_prompt=False,
    build_cmd=_codex_build_cmd,
    parse_line=_codex_parse_line,
    extract_session_id=lambda data: data.get("thread_id") if data.get("type") == "thread.started" else None,
    # Best-effort (sourced from public openai/codex issues, not forced live).
    usage_limit_markers=(
        "usage_limit_reached",
        "the usage limit has been reached",
        "429 too many requests",
        "rate_limit_exceeded",
        "exceeded retry limit",
    ),
    # No bare "401" here either — same false-positive-on-random-hex-UUID risk as Copilot
    # (see the comment on COPILOT.auth_error_markers).
    auth_error_markers=(
        "not logged in",
        "codex login",
        "invalid api key",
        "unauthorized",
    ),
    # No transient-overload wording confirmed live for this backend yet — left empty
    # rather than guessing (see the false-positive caution above for auth_error_markers).
    overloaded_markers=(),
    friendly_auth_error_message=_codex_friendly_auth_error_message,
    reasoning_effort_choices=lambda model: CODEX_MODEL_REASONING_LEVELS.get(model.strip().lower(), CODEX_DEFAULT_EFFORT_LEVELS),
)


BACKENDS: dict[str, Backend] = {
    CLAUDE.name: CLAUDE,
    COPILOT.name: COPILOT,
    CODEX.name: CODEX,
}


def get_backend_def(name: str) -> Backend:
    """Return the Backend for `name`, falling back to CLAUDE for an unrecognized/legacy
    value (e.g. a config.json predating multi-backend support)."""
    return BACKENDS.get(name, CLAUDE)
