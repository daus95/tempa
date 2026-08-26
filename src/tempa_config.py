"""Shared configuration, paths, and workspace/sources/model resolution.

This module is the single source of truth for config.json access and for turning the
`workspace`/`sources`/`models` config into concrete absolute paths and model ids. It is
imported by BOTH the CLI (tempa.py and its tempa_* modules) and the web dashboard
(dashboard_ui.py and its dashboard_* modules).

It intentionally depends on the standard library ONLY (json, pathlib). That keeps it a
dependency-free leaf of the import graph, so the dashboard can share this logic instead of
re-implementing it — without ever importing tempa.py (which would create a cycle, since
tempa.py imports dashboard_ui).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Modules live in src/, so anchor to the parent of src/ (the Tempa install root) for
# Tempa's own bootstrap files.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
WORKING_DIR = SCRIPT_DIR.parent
# Prompt templates are a resource shipped with the tool (like the dashboard's assets/), so
# they live inside src/ — anchored to this module's own folder, not SCRIPT_DIR (the root).
# One .md file per prompt (readable, editable); loaded via load_prompt() in tempa_prompts.
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"

# Tiny bootstrap pointer, at Tempa's own install root: one line holding the absolute path
# of the currently active workspace, so Tempa knows which workspace's own config.json to
# load BEFORE it can load any config.json (workspace.root itself lives inside that file).
# Absent = no active workspace (fresh install, or after `close-folder`).
ACTIVE_WORKSPACE_POINTER = SCRIPT_DIR / ".active-workspace"

# MRU list of workspace roots ever opened via `tempa init`, newest first — lives beside
# ACTIVE_WORKSPACE_POINTER at Tempa's own install root (NOT under _tempa_dir(), which
# follows whichever workspace is currently active and would make this fragment per
# workspace instead of surviving across them). Powers the dashboard Home page's "recent
# working folders" list, which is exactly the situation where no workspace is active yet
# and _tempa_dir() would resolve to install-root scratch space anyway. Read/write helpers
# below.
WORKSPACE_HISTORY_PATH = SCRIPT_DIR / ".workspace-history.json"
WORKSPACE_HISTORY_MAX = 10

# Per-workspace state (config.json + logs/ + qa/ + verify/ + specs/) all live under this
# hidden sub-folder, INSIDE the workspace being automated — not inside Tempa's own install —
# so each workspace keeps its own config/history across switches. Until a workspace is
# active, everything falls back to this same sub-folder inside Tempa's own install (SCRIPT_DIR),
# purely as scratch space so commands like `set-model`/`test` still work pre-`init`.
TEMPA_SUBDIR_NAME = ".tempa"

# The value stored in an epic's "last_round_note_kind" when its note is not a conclusion about
# the code but an intention the round never got to act on. Lives here rather than in either
# module that uses it: tempa_session_outcome writes it, tempa_prompts reads it, and both already
# import this module.
LAST_ROUND_NOTE_UNFINISHED_CHECK = "unfinished_check"


def get_active_workspace_root() -> Path | None:
    """Return the active workspace's absolute root path, or None if none is active."""
    if not ACTIVE_WORKSPACE_POINTER.exists():
        return None
    text = ACTIVE_WORKSPACE_POINTER.read_text(encoding="utf-8").strip()
    return Path(text) if text else None


def set_active_workspace_root(root: Path) -> None:
    """Point Tempa at `root` as the active workspace (used by `tempa init`)."""
    ACTIVE_WORKSPACE_POINTER.write_text(str(root), encoding="utf-8")


def clear_active_workspace_root() -> None:
    """Drop the active-workspace pointer (used by `tempa close-folder`). The workspace's
    own .tempa/ folder (config.json/logs/qa/verify/specs) is left untouched on disk —
    only the pointer to it is removed, so reopening it later resumes where it left off."""
    if ACTIVE_WORKSPACE_POINTER.exists():
        ACTIVE_WORKSPACE_POINTER.unlink()


def _history_key(root: str | Path) -> str:
    """Normalize a workspace root for de-duplication in the history list, using the host
    platform's own filesystem semantics (os.path.normcase/normpath) — on Windows this
    folds case and `/`/`\\` separator differences (`C:\\A\\b` vs `c:/a/B`) so they don't
    create two entries for the same folder; elsewhere both are already meaningful
    differences and normcase is a no-op."""
    return os.path.normcase(os.path.normpath(str(root)))


def read_workspace_history() -> list[dict]:
    """Read the recent-workspaces list, tolerantly: any read/parse error, a non-list
    payload, or an entry that isn't a dict with a non-empty string "root" is dropped
    rather than raised, mirroring read_config_safe()'s degrade-gracefully contract. Always
    newest-first and capped at WORKSPACE_HISTORY_MAX, regardless of what's on disk."""
    try:
        raw = json.loads(WORKSPACE_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        root = item.get("root")
        if not isinstance(root, str) or not root:
            continue
        opened_at = item.get("opened_at")
        if not isinstance(opened_at, (int, float)) or isinstance(opened_at, bool):
            opened_at = 0
        entries.append({"root": root, "opened_at": opened_at})
    return entries[:WORKSPACE_HISTORY_MAX]


def _save_workspace_history(entries: list[dict]) -> None:
    """Write the history list atomically (temp file + os.replace(), same pattern as
    save_config()). Fails open — a read-only or missing install folder must not turn
    "remember this folder" into a crashed init/close-folder."""
    with contextlib.suppress(OSError):
        WORKSPACE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=WORKSPACE_HISTORY_PATH.parent, prefix=".workspace-history.", suffix=".json.tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, WORKSPACE_HISTORY_PATH)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise


def record_workspace_history(root: str | Path) -> None:
    """Move `root` to the front of the recent-workspaces list (adding it if new), stamped
    with the current time, and trim to WORKSPACE_HISTORY_MAX. Called once a workspace is
    actually registered (`tempa init`) and again when it's closed (`tempa close-folder`) —
    see those callers for why both matter. No cross-process lock: unlike config.json, a
    lost update here just means one dashboard's MRU bump was overwritten by another's,
    never a silently-dropped user decision, so the plain read-modify-write is enough."""
    root_str = str(Path(root))
    key = _history_key(root_str)
    entries = [e for e in read_workspace_history() if _history_key(e["root"]) != key]
    entries.insert(0, {"root": root_str, "opened_at": time.time()})
    _save_workspace_history(entries[:WORKSPACE_HISTORY_MAX])


def remove_workspace_history(root: str | Path) -> bool:
    """Drop the entry matching `root` from the recent-workspaces list. Returns whether
    anything was actually removed."""
    key = _history_key(root)
    entries = read_workspace_history()
    kept = [e for e in entries if _history_key(e["root"]) != key]
    if len(kept) == len(entries):
        return False
    _save_workspace_history(kept)
    return True


def _tempa_dir() -> Path:
    """The active workspace's `.tempa/` folder, or Tempa's own scratch `.tempa/` folder
    when no workspace is active yet."""
    root = get_active_workspace_root()
    base = root if root is not None else SCRIPT_DIR
    return base / TEMPA_SUBDIR_NAME


def get_config_path() -> Path:
    return _tempa_dir() / "config.json"


def get_logs_dir() -> Path:
    return _tempa_dir() / "logs"


def get_qa_dir() -> Path:
    return _tempa_dir() / "qa"


def get_verify_dir() -> Path:
    return _tempa_dir() / "verify"


def get_decisions_dir() -> Path:
    """Where a decision answered from outside the runner is recorded until the runner has
    acted on it (see tempa_decisions.record_answer). One file per answered feature — the same
    "one writer, one reader" shape as the graceful-stop sentinels below, and for the same
    reason: config.json alone cannot carry state written from another process."""
    return _tempa_dir() / "decisions"


# ---------------------------------------------------------------------------
# Graceful stop — the one cross-process channel between the dashboard and a running
# `tempa implement` / `tempa clarify --finalize`.
#
# The dashboard never runs those in-process: it spawns `python tempa.py <command>` as a
# child and only reads its stdout (see dashboard_runs.py), so an in-memory flag can't
# reach the runner. A key in config.json can't either — the runner's session threads
# read-modify-write that file constantly, so a flag written from outside would be lost
# the next time the runner saved. A sentinel file has exactly one writer and one reader
# and races with nothing.
#
# Presence of the file IS the request; its contents are never read. Every helper below
# fails open (does nothing / reports "no request") so a permission or disk error can
# never turn into a stalled run or a crashed dashboard — the worst case is that a
# graceful stop has to be repeated as an immediate one.
# ---------------------------------------------------------------------------
def get_graceful_stop_path(kind: str) -> Path:
    """Sentinel path for `kind` ("implement" or "clarify"). Lives next to config.json so
    it follows the active workspace, and so the CLI and the dashboard — separate
    processes that both resolve it through _tempa_dir() — always mean the same file."""
    return _tempa_dir() / f"graceful-stop-{kind}"


def request_graceful_stop(kind: str) -> None:
    """Ask the running `kind` process to stop once the session in progress finishes."""
    path = get_graceful_stop_path(kind)
    # The timestamp is only ever read by a human wondering where a stray file came from —
    # every check below is presence-only.
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().isoformat(), encoding="utf-8")


def graceful_stop_requested(kind: str) -> bool:
    try:
        return get_graceful_stop_path(kind).exists()
    except OSError:
        return False


def clear_graceful_stop(kind: str) -> None:
    """Drop any pending request. Called when one is honoured, cancelled, or when a fresh
    run starts (so a sentinel left behind by a crash can't stop the next run instantly)."""
    with contextlib.suppress(OSError):
        get_graceful_stop_path(kind).unlink(missing_ok=True)


def get_principles_path() -> Path:
    """The workspace's architecture principles document — project-wide rules the user writes
    once, injected into every stage's prompt (see tempa_prompts.build_prompt). Optional:
    an absent file simply means no principles are applied."""
    return _tempa_dir() / "architecture-principles.md"


def read_principles() -> str:
    """Return the architecture principles text, or "" if unset/blank/unreadable."""
    try:
        return get_principles_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


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
    "apps": "src",
    # Infrastructure scripts (e.g. docker compose).
    "infra": "infra",
    # Sub-folders holding past specification files no longer in use.
    "archive": "archive",
}

# Relative suffix under workspace.specs for each of these `sources` folders (not
# just "specs" because these need one more nesting level, e.g. specs/pbi/epics).
# Used by get_sources() to derive a default when `sources.<key>` isn't set.
DEFAULT_SOURCE_SUFFIXES = {
    "prd": "prd",
    "epics": "pbi/epics",
    "clarifications": "clarifications",
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
# - clarify      : PRD clarification EVALUATE session (clarify) — the stage that decides
#   what's ambiguous/conflicting, so it keeps the strongest default model.
# - clarify_apply: apply resolutions to the PRD/spec + auto-answer (clarify --apply,
#   --auto-answer, and the apply half of --finalize) — mechanical work (copy an already-
#   decided answer/recommendation into the PRD, or pick one from context), so it defaults
#   to a cheaper model. A full stage of its own (has its own backends/reasoning_efforts
#   entries too, same as clarify/plan/implement) — the optimal backend/effort for
#   mechanical apply work isn't necessarily the same as for evaluate.
# - plan         : epic/feature/task planning session (run automatically by implement /
#   implement --replan) — its output determines the validity of every implementation that
#   follows, so it keeps the strongest default model, same as clarify.
# - implement    : implementation session (implement), including QA and verify — runs
#   repeatedly at high volume, so it defaults to a cheaper/faster model.
DEFAULT_MODELS = {
    "clarify": "claude-opus-5",
    "clarify_apply": "claude-sonnet-5",
    "plan": "claude-opus-5",
    "implement": "claude-sonnet-5",
}

# Friendly aliases → full model id, so users can type e.g. "opus-5" or "sonnet-5".
# Claude-only: Copilot/Codex model catalogs move independently of Tempa and are passed
# through as-is (see _resolve_model_alias / get_backend).
MODEL_ALIASES = {
    "opus-5": "claude-opus-5",
    "opus": "claude-opus-5",
    "sonnet-5": "claude-sonnet-5",
    "sonnet": "claude-sonnet-5",
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "haiku": "claude-haiku-4-5-20251001",
    "fable-5": "claude-fable-5",
    "fable": "claude-fable-5",
}

# Which CLI backend drives each harness stage. Stored under the "backends" key in
# config.json. One of "claude" (Claude Code), "copilot" (GitHub Copilot CLI), or "codex"
# (OpenAI Codex CLI) — see tempa_backend.BACKENDS for what each one actually runs.
DEFAULT_BACKENDS = {
    "clarify": "claude",
    "clarify_apply": "claude",
    "plan": "claude",
    "implement": "claude",
}

# Reasoning effort per harness stage. Stored under the "reasoning_efforts" key in
# config.json. "" means no override — the backend CLI/model's own default is used. A
# non-empty value must be one of the stage's backend+model's valid choices (see
# tempa_backend.is_valid_reasoning_effort) — enforced in dashboard_server.py and
# tempa_commands.set_efforts, not here (this module has no tempa_backend dependency).
DEFAULT_REASONING_EFFORTS = {
    "clarify": "",
    "clarify_apply": "",
    "plan": "",
    "implement": "",
}

# Valid values for config.json's "implementation_start_requirement" (the dashboard
# Settings "Start Implementation requires" control) — how strictly Start Implementation
# is gated on the most recent evaluation round's clarification findings:
#   "no_critical_or_major" (default): zero critical AND zero major findings required —
#     the original, safest behavior.
#   "no_critical": zero critical findings required; major findings may remain open.
#   "none": no clarification-findings condition at all (clarification must still have
#     been run at least once — see _implement_readiness_status in
#     dashboard_clarify_parse.py).
IMPLEMENTATION_START_REQUIREMENTS = ("no_critical_or_major", "no_critical", "none")
DEFAULT_EMAIL_NOTIFICATION_EVENTS = (
    "authentication_required", "implementation_failed", "plan_failed", "session_limit_reached",
    "qa_limit_reached", "qa_oscillation_detected", "clarification_answers_required", "clarification_limit_reached",
    "clarification_failed", "confirmation_required", "verification_failed", "backend_test_failed",
)


# Fresh-install / deleted-file fallback for load_config() below. Mirrors the shape
# documented in docs/config-json.md — every key a brand-new config.json should have,
# with no workspace linked yet (empty root) and no run history.
DEFAULT_CONFIG = {
    "models": dict(DEFAULT_MODELS),
    "backends": dict(DEFAULT_BACKENDS),
    "reasoning_efforts": dict(DEFAULT_REASONING_EFFORTS),
    "features_per_session": 3,
    "resume_implementation_sessions": True,
    "commit_after_qa_pass": True,
    "max_session_run": 30,
    "max_clarification_run": 20,
    "finalize_no_progress_rounds": 5,
    "finalize_checkpoint_rounds": 3,
    "finalize_checkpoint_commit": True,
    "implement_no_progress_rounds": 2,
    "qa_loop_strikes": 2,
    "max_qa_fail_rounds": 6,
    "clarify_overlay_warn_findings": 25,
    "usage_limit_retry_wait_sec": 1800,
    "usage_limit_heartbeat_sec": 300,
    "server_overloaded_retry_wait_sec": 300,
    "backend_background_wait_sec": 3600,
    "terminate_leftover_processes": True,
    "poll_interval_sec": 60,
    "last_clarification_findings": {"critical": 0, "major": 0, "minor": 0},
    "last_clarification_round": 0,
    "last_clean_evaluation_at": 0,
    "last_auto_answer": 0,
    "allow_finalize_with_critical": False,
    "skip_minor_findings": True,
    "clarify_severity_phases": True,
    "critical_phase_max_rounds": 10,
    "implementation_start_requirement": "no_critical_or_major",
    "notifications": {
        "email": {
            "enabled": False,
            "provider": "custom",
            "smtp_host": "",
            "smtp_port": 587,
            "security": "starttls",
            "smtp_username": "",
            "smtp_password": "",
            "from": "tempa-noreply@tempa-ai.com",
            "recipients": [],
            "username_env": "TEMPA_SMTP_USERNAME",
            "password_env": "TEMPA_SMTP_PASSWORD",
            "timeout_seconds": 10,
            "events": list(DEFAULT_EMAIL_NOTIFICATION_EVENTS),
        },
    },
    "epic": [],
    "workspace": dict(DEFAULT_WORKSPACE),
}


def get_email_notifications(config: dict) -> dict:
    """Return normalized, non-secret email-notification settings."""
    defaults = copy.deepcopy(DEFAULT_CONFIG["notifications"]["email"])
    candidate = (config.get("notifications") or {}).get("email")
    if isinstance(candidate, dict):
        defaults.update(candidate)
    defaults["enabled"] = bool(defaults["enabled"])
    defaults["recipients"] = [str(value).strip() for value in defaults["recipients"]
                              if isinstance(value, str) and value.strip()]
    defaults["events"] = [str(value) for value in defaults["events"] if isinstance(value, str)]
    return defaults


def load_config() -> dict:
    """Load config.json, transparently creating it from DEFAULT_CONFIG if it doesn't
    exist yet (e.g. a brand-new workspace, or a fresh clone) so every caller gets a usable
    dict instead of a FileNotFoundError. Resolved fresh on every call via get_config_path(),
    so it always reflects whichever workspace is currently active (see
    get_active_workspace_root()).

    The default is only written to disk when a workspace is actually active. With no
    active workspace (fresh install, or after `close-folder`), read-only callers like
    `--help`/`status`/`dashboard` would otherwise recreate a useless config.json in
    Tempa's own install folder just by being run. Commands that intentionally use that
    folder as pre-init scratch space (`set-model`, `set-folders`, `test`) still persist
    there fine — they call save_config() themselves once the user actually sets something."""
    config_path = get_config_path()
    if not config_path.exists():
        config = copy.deepcopy(DEFAULT_CONFIG)
        if get_active_workspace_root() is not None:
            save_config(config)
        return config
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Write config.json atomically: serialize to a temp file in the same directory, then
    os.replace() it into place. A reader (this module's own load_config/read_config_safe,
    or the spawned CLI agent's own file-read) never observes a partially-written file — it
    either sees the old complete content or the new complete content, never a torn write.
    This does NOT eliminate the separate lost-update race against the spawned agent process
    editing the same file concurrently (a load-modify-save cycle here can still overwrite
    whatever the agent wrote in between with the version Tempa read before it) — that would
    need cross-process locking and is intentionally out of scope here."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, prefix=".config.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, config_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise


def read_config_safe() -> dict:
    """Read config.json fresh and tolerantly: returns {} on any read/parse error, and
    always {} rather than a non-dict. Unlike load_config(), never raises — used where a
    missing/half-written config should degrade gracefully (e.g. the dashboard re-reading
    after workspace.root changes, so it reflects the new location without a restart)."""
    try:
        config = json.loads(get_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return config if isinstance(config, dict) else {}


# ---------------------------------------------------------------------------
# Cross-process lock for a read-modify-write of config.json.
#
# save_config() above is atomic against a torn *read* but not against a lost *update*: two
# processes that each load, edit and save keep only the second one's version of everything
# the first one touched. That is tolerated for the runner's own writes (see its docstring),
# but not for a write arriving from outside the runner while a run is going — the dashboard
# answering a blocked feature's question has to edit one field of a file that the runner and
# the spawned agent are both writing to, and losing it means the user's decision silently
# never happened.
#
# O_CREAT|O_EXCL is the whole mechanism: creating the file IS acquiring the lock, on Windows
# and POSIX alike, with no platform branch and no dependency. Its one weakness is that a
# process killed while holding it leaves the file behind, so a lock older than
# _STALE_LOCK_SEC is broken — the critical section is a read, a field assignment and a write,
# so an age measured in seconds means a crash, not a slow writer.
#
# Acquisition FAILS OPEN: on timeout the body runs unlocked rather than raising, the same way
# every other filesystem helper here degrades instead of stopping a run. An unlocked surgical
# write is still far safer than the load-modify-save it replaces — it re-reads first and
# touches one field — whereas refusing to write would drop a decision the user already made.
# ---------------------------------------------------------------------------
_STALE_LOCK_SEC = 30.0
_LOCK_POLL_SEC = 0.05


def get_config_lock_path() -> Path:
    """Lock file guarding a read-modify-write of config.json. Lives beside config.json so it
    follows the active workspace, exactly like the graceful-stop sentinels."""
    return _tempa_dir() / "config.lock"


def _break_stale_lock(path: Path) -> None:
    """Drop a lock file left behind by a process that died holding it."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return
    if age > _STALE_LOCK_SEC:
        with contextlib.suppress(OSError):
            path.unlink()


@contextlib.contextmanager
def config_lock(timeout: float = 10.0):
    """Hold the config.json lock for the duration of the block.

    Yields True when the lock was actually acquired and False when it timed out and the body
    is running unlocked (see the fail-open rationale above), so a caller that wants to log the
    difference can and one that doesn't can ignore it."""
    path = get_config_lock_path()
    deadline = time.monotonic() + max(timeout, 0.0)
    fd = None
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            # Held by someone else, or — on Windows — momentarily unopenable while its holder
            # releases it. Either way the answer is to wait and retry.
            _break_stale_lock(path)
        except OSError:
            # An unwritable .tempa/ is not a reason to lose the write: proceed unlocked
            # rather than spinning until the deadline over a condition that won't clear.
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(_LOCK_POLL_SEC)
    try:
        if fd is not None:
            # Contents are only ever read by a human wondering who is holding it.
            with contextlib.suppress(OSError):
                os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
        yield fd is not None
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                path.unlink()


def update_config(mutate) -> bool:
    """Apply `mutate` to config.json as one locked read-modify-write; report whether it saved.

    The point is the re-read: the config handed to `mutate` is loaded from disk INSIDE the
    lock, so a caller can never write back a document it read before another process edited
    it. `mutate(config)` returns True to have the result saved and False to leave the file
    untouched. Use this rather than load_config()/save_config() for any write that can happen
    while a run is in progress."""
    with config_lock():
        config = read_config_safe() or load_config()
        if not mutate(config):
            return False
        save_config(config)
        return True


def get_workspace(config: dict) -> dict:
    """Return the workspace config merged over DEFAULT_WORKSPACE, so any missing
    key falls back to its default."""
    workspace = dict(DEFAULT_WORKSPACE)
    workspace.update(config.get("workspace", {}))
    return workspace


def resolve_workspace_paths(config: dict) -> dict:
    """Resolve every workspace folder to an absolute path. `root` is returned as-is;
    `specs` is joined onto root/.tempa (it's Tempa-managed state, kept alongside
    config.json/logs/qa/verify); every other folder is joined directly onto root.
    Returns {} if root is not configured."""
    workspace = get_workspace(config)
    root = workspace.get("root", "")
    if not root:
        return {}
    root_path = Path(root)
    resolved = {"root": str(root_path)}
    for key in DEFAULT_WORKSPACE:
        if key == "root":
            continue
        if key == "specs":
            resolved[key] = str(root_path / TEMPA_SUBDIR_NAME / workspace[key])
        else:
            resolved[key] = str(root_path / workspace[key])
    return resolved


def workspace_is_writable(root: str) -> bool:
    """Best-effort check that the current OS user can write files under `root`. All three
    CLI backends (claude/copilot/codex) run as this same OS user (see
    tempa_session._stream_backend_process), so this is what actually gates whether any of
    them can write to the workspace — their own --dangerously-skip-permissions-style flags
    only bypass in-app approval prompts, not OS filesystem permissions. Returns False for an
    unset/non-existent root or on any OSError (e.g. read-only mount, permission denied)."""
    if not root:
        return False
    path = Path(root)
    if not path.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".tempa-write-check-"):
            pass
    except OSError:
        return False
    return True


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
    """Return the `sources` dict (prd/docs/epics/apps/clarifications), each
    resolved to an absolute path. Values are derived from `workspace` by
    default — `docs`/`apps` mirror workspace.docs/workspace.apps, and
    `prd`/`epics`/`clarifications` default to that suffix under workspace.specs
    (see DEFAULT_SOURCE_SUFFIXES) — so config.json doesn't need to duplicate
    them. An explicit `sources.<key>` entry in config.json still overrides its
    default (relative values resolved via resolve_source_path, so an absolute
    override is used as-is). Use this everywhere instead of reading
    config["sources"] directly."""
    workspace_resolved = resolve_workspace_paths(config)
    specs_dir = resolve_specs_dir(config)
    defaults = {
        "docs": workspace_resolved.get("docs", ""),
        "apps": workspace_resolved.get("apps", ""),
    }
    for key, suffix in DEFAULT_SOURCE_SUFFIXES.items():
        defaults[key] = str(specs_dir / suffix)

    raw = config.get("sources", {})
    result = dict(defaults)
    for key, value in raw.items():
        if value:
            result[key] = resolve_source_path(config, value)
    return result


def resolve_specs_dir(config: dict) -> Path:
    """Return the absolute path of the specifications folder (workspace.specs).

    Lives under workspace.root/.tempa (alongside config.json/logs/qa/verify) when root is
    configured. Otherwise mirrors how the agent runner resolves relative paths: joined onto
    WORKING_DIR (where the agent is run), so `spec` points at the same folder the rest of
    the pipeline reads/writes."""
    workspace = get_workspace(config)
    specs_rel = workspace.get("specs") or "specs"
    root = workspace.get("root")
    if root:
        return Path(root) / TEMPA_SUBDIR_NAME / specs_rel
    specs_path = Path(specs_rel)
    return specs_path if specs_path.is_absolute() else WORKING_DIR / specs_rel


def resolve_prd_dir(config: dict) -> Path:
    """Return the absolute path of the PRD folder (sources.prd). Shared by every
    entry point that opens the dashboard's Specification section."""
    return Path(get_sources(config)["prd"])


def resolve_clar_dir(config: dict) -> Path:
    """Return the absolute path of the clarifications folder (sources.clarifications)."""
    return Path(get_sources(config)["clarifications"])


def resolve_epics_dir(config: dict) -> Path:
    """Return the absolute path of the epic-spec folder (sources.epics).

    This is what QA reads an epic's requirements from, and it is a SIBLING of the PRD folder the
    dashboard's Specification tree is rooted at — so nothing under it is reachable from that
    tree. The dashboard reaches an epic's own spec through here instead, linked straight off the
    epic's card (see dashboard_api_spec.read_epic_spec)."""
    return Path(get_sources(config)["epics"])


def _resolve_model_alias(value: str) -> str:
    """Map a friendly alias (e.g. "opus-5") to its full model id. If `value` is not
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


def get_backends(config: dict) -> dict:
    """Return the per-stage backends merged over DEFAULT_BACKENDS (missing stage → default)."""
    backends = dict(DEFAULT_BACKENDS)
    backends.update(config.get("backends", {}))
    return backends


def get_backend(config: dict, stage: str) -> str:
    """Return the CLI backend configured for a stage (clarify | plan | implement):
    "claude" | "copilot" | "codex"."""
    return get_backends(config).get(stage, DEFAULT_BACKENDS.get(stage, "claude"))


def get_reasoning_efforts(config: dict) -> dict:
    """Return the per-stage reasoning efforts merged over DEFAULT_REASONING_EFFORTS
    (missing stage → default, i.e. "" / no override)."""
    efforts = dict(DEFAULT_REASONING_EFFORTS)
    efforts.update(config.get("reasoning_efforts", {}))
    return efforts


def get_reasoning_effort(config: dict, stage: str) -> str:
    """Return the reasoning effort configured for a stage (clarify | plan | implement).
    "" means no override — the backend CLI/model's own default is used."""
    return get_reasoning_efforts(config).get(stage, DEFAULT_REASONING_EFFORTS.get(stage, ""))


def get_implementation_start_requirement(config: dict) -> str:
    """Return config.json's "implementation_start_requirement" — one of
    IMPLEMENTATION_START_REQUIREMENTS — falling back to the default
    ("no_critical_or_major") for a missing or invalid value."""
    value = config.get("implementation_start_requirement")
    return value if value in IMPLEMENTATION_START_REQUIREMENTS else "no_critical_or_major"


def get_skip_minor_findings(config: dict) -> bool:
    """Return config.json's "skip_minor_findings" (dashboard toggle + CLI --skip-minor
    default), defaulting to True for a missing value."""
    return bool(config.get("skip_minor_findings", True))


def get_clarify_severity_phases(config: dict) -> bool:
    """Return config.json's "clarify_severity_phases" (default True) — whether clarification
    walks the severities in phases (every critical first, then major, then minor) instead of
    evaluating them all in one round.

    Off reproduces the pre-phases behavior exactly: every round is scoped to critical+major
    (or all, per skip_minor_findings) and the run ends when both reach zero. See
    tempa_clarify._PHASE_SCOPES for what the switch actually changes about a round."""
    return bool(config.get("clarify_severity_phases", True))


def severity_sweep_pending(config: dict) -> bool:
    """True when the most recent evaluate pass was scoped narrower than "did anything major
    turn up" — i.e. it was a critical-only round of the critical phase.

    Such a round records major=0 because it never looked for majors, so the Start
    Implementation gate must not read that zero as an answer (see
    dashboard_clarify_parse._implement_readiness_status). A config.json with no
    "last_evaluation_scope" at all predates severity phases: its last round was the old
    critical+major one, so it is NOT pending and an already-open gate stays open."""
    return config.get("last_evaluation_scope", "all") not in ("critical_major", "all")


def get_critical_phase_max_rounds(config: dict) -> int | float:
    """Return config.json's "critical_phase_max_rounds" (default 10) — how many answering
    rounds a `clarify --finalize` run may spend in the critical phase, across the whole run,
    before it stops and asks for a human.

    Bounded separately from finalize_no_progress_rounds because the two catch different
    things: that one catches a loop making no progress, this one catches a loop that is
    making progress but has spent long enough on findings a human should probably be
    deciding — a critical is, by the rubric, the specification being unbuildable.

    The default was measured, not guessed. Eight manual rounds against a 256-line PRD turned
    up a critical derivable from the ORIGINAL spec — not fallout from an earlier round's
    answer — in each of rounds 4 through 8. An earlier default of 6 would have cut three of
    those off while the specification was still yielding one real defect per round.

    10 leaves headroom past that observed run and still binds well before
    max_clarification_run's own default of 20, so it remains a guard rather than a formality.
    Raise it for a specification whose critical sweep is still finding original defects when
    it stops; the log says which round found what."""
    return _get_positive_number(config, "critical_phase_max_rounds", DEFAULT_CONFIG["critical_phase_max_rounds"])


# "last_severity_phase", "clarify_phase_clean_rounds" and "last_evaluation_scope" are
# deliberately NOT in DEFAULT_CONFIG: they are a run's state, not settings, and every reader
# above treats a missing key as "no clarification round has run under severity phases yet".
# Seeding them would make a brand-new workspace look like one mid-sweep — and would make
# `tempa clear` report stale state to clear on a workspace that has never run anything.


def _get_positive_number(config: dict, key: str, default: int) -> int | float:
    """Return config[key] if it's a positive int/float, else `default` (missing/invalid
    value). Accepts float as well as int — not because config.json is expected to store
    fractional seconds, but so tests can monkeypatch load_config with sub-second values
    (e.g. 0.01) instead of actually sleeping whole seconds."""
    value = config.get(key, default)
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if is_number and value > 0 else default


def get_usage_limit_retry_wait_sec(config: dict) -> int | float:
    """Return config.json's "usage_limit_retry_wait_sec" — how long to wait before
    retrying a clarify/implement/QA/verify step after a usage-limit stop (see
    tempa_session.wait_out_usage_limit) — defaulting to 1800 (30 minutes)."""
    return _get_positive_number(config, "usage_limit_retry_wait_sec", DEFAULT_CONFIG["usage_limit_retry_wait_sec"])


def get_usage_limit_heartbeat_sec(config: dict) -> int | float:
    """Return config.json's "usage_limit_heartbeat_sec" — how often a "still waiting"
    heartbeat is logged during the usage-limit wait above — defaulting to 300 (5 minutes)."""
    return _get_positive_number(config, "usage_limit_heartbeat_sec", DEFAULT_CONFIG["usage_limit_heartbeat_sec"])


def get_server_overloaded_retry_wait_sec(config: dict) -> int | float:
    """Return config.json's "server_overloaded_retry_wait_sec" — how long to wait before
    retrying after the backend's API reports itself overloaded (see
    tempa_session.wait_out_server_overload) — defaulting to 300 (5 minutes)."""
    return _get_positive_number(
        config, "server_overloaded_retry_wait_sec", DEFAULT_CONFIG["server_overloaded_retry_wait_sec"]
    )


def get_backend_background_wait_sec(config: dict) -> int | float:
    """Return config.json's "backend_background_wait_sec" — how long a backend CLI should
    wait for background work its own turn left running (a delegated sub-agent, a
    backgrounded shell) before killing it and exiting — defaulting to 3600 (1 hour).

    Unlike every other `*_sec` setting here, 0 is meaningful rather than invalid: it's the
    backend CLI's documented "wait indefinitely" value. That is deliberately NOT the
    default — a session that leaves a dev server running would then hang the runner with no
    upper bound, whereas a finite ceiling still ends it eventually (and
    tempa_session._is_background_terminated_text makes Tempa resume rather than fail when
    it does). Only positive numbers and 0 are accepted; anything else falls back to the
    default. Converted to each backend's own unit by Backend.background_wait_env."""
    value = config.get("backend_background_wait_sec", DEFAULT_CONFIG["backend_background_wait_sec"])
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if is_number and value >= 0 else DEFAULT_CONFIG["backend_background_wait_sec"]


def get_terminate_leftover_processes(config: dict) -> bool:
    """Return config.json's "terminate_leftover_processes" (default True) — whether the
    processes a backend CLI session leaves running (a dev server it started to check its own
    work, a file watcher, a test runner, a build daemon) are terminated when that session
    ends, instead of being orphaned to the OS.

    Complements backend_background_wait_sec above rather than replacing it: that is how long
    the CLI itself waits for the background work *it* is tracking before killing it and
    exiting, and it only helps while the CLI is still alive to act. This covers whatever is
    still running once the CLI process is gone — including what it never tracked at all.

    Read fresh immediately before each spawn (tempa_session._stream_backend_process) and
    fixed for that process tree's lifetime, so a change applies from the next session
    onward. An opt-out for the user who deliberately wants a session's processes to
    outlive it."""
    value = config.get("terminate_leftover_processes")
    return bool(value) if isinstance(value, bool) else True


def get_resume_implementation_sessions(config: dict) -> bool:
    """Return config.json's "resume_implementation_sessions" (default True) — whether a
    continuation/require_fixing implementation session should --resume the epic's
    previous session (reusing its already-paid-for context: the epic spec, the code it
    already read) instead of always starting cold. An escape hatch for the rare case
    where resuming misbehaves for a given backend/workspace."""
    value = config.get("resume_implementation_sessions")
    return bool(value) if isinstance(value, bool) else True


def get_commit_after_qa_pass(config: dict) -> bool:
    """Return config.json's "commit_after_qa_pass" (default True) — whether Tempa should
    `git commit` the workspace right after an epic's QA verdict is a genuine pass (see
    tempa_git.commit_workspace_changes, called from run_qa_session). An opt-out for
    workspaces where the user would rather commit by hand."""
    value = config.get("commit_after_qa_pass")
    return bool(value) if isinstance(value, bool) else True


def get_finalize_no_progress_rounds(config: dict) -> int | float:
    """Return config.json's "finalize_no_progress_rounds" — how many `clarify --finalize`
    rounds in a row may fail to reduce the critical+major finding count before the loop
    gives up and asks for human answers (see run_clarify_finalize) — defaulting to 5."""
    return _get_positive_number(config, "finalize_no_progress_rounds", DEFAULT_CONFIG["finalize_no_progress_rounds"])


def get_finalize_checkpoint_rounds(config: dict) -> int | None:
    """Return config.json's "finalize_checkpoint_rounds" — how many answering rounds of a
    `clarify --finalize` run may pile up in the pending overlay before the loop stops to
    write them into the PRD and commit (see _run_checkpoint in tempa_clarify.py) —
    defaulting to 3.

    None means "never checkpoint", which is the behavior finalize had before checkpoints
    existed: nothing is written until the closing compaction. Unlike every other limit here
    that is a reason _get_positive_number can't be reused — it has no way to express
    "disabled", and blank in the Settings form has to mean off rather than "fall back to 3".
    A MISSING key still means the default, though: that's a config.json written before this
    setting existed, not somebody switching checkpoints off."""
    if "finalize_checkpoint_rounds" not in config:
        return DEFAULT_CONFIG["finalize_checkpoint_rounds"]
    value = config.get("finalize_checkpoint_rounds")
    if value is None:
        return None
    is_number = isinstance(value, int) and not isinstance(value, bool)
    return value if is_number and value > 0 else DEFAULT_CONFIG["finalize_checkpoint_rounds"]


def get_finalize_checkpoint_commit(config: dict) -> bool:
    """Return config.json's "finalize_checkpoint_commit" (default True) — whether each
    `clarify --finalize` checkpoint, and the end of a successful run, should `git commit` the
    workspace (see tempa_git.commit_workspace_changes). An opt-out for workspaces where the
    user would rather commit by hand, exactly like commit_after_qa_pass above."""
    value = config.get("finalize_checkpoint_commit")
    return bool(value) if isinstance(value, bool) else True


def get_qa_loop_strikes(config: dict) -> int | float:
    """Return config.json's "qa_loop_strikes" — how many QA rounds in a row must show a
    regression or a repeated failure set before an epic is declared stuck cycling through the
    QA gate (see tempa_qa_history.detect_qa_loop) — defaulting to 2. Raise it to give an epic
    more rope before the guard stops the run."""
    return _get_positive_number(config, "qa_loop_strikes", DEFAULT_CONFIG["qa_loop_strikes"])


def get_max_qa_fail_rounds(config: dict) -> int | float:
    """Return config.json's "max_qa_fail_rounds" — how many times one epic may fail QA without
    ever passing before the loop guard stops it regardless of pattern (see
    tempa_qa_history.detect_qa_loop) — defaulting to 6. This is the backstop for the case where
    the QA agent doesn't write per-feature statuses at all, leaving the pattern rules nothing to
    fingerprint a round by."""
    return _get_positive_number(config, "max_qa_fail_rounds", DEFAULT_CONFIG["max_qa_fail_rounds"])


def get_clarify_overlay_warn_findings(config: dict) -> int | float:
    """Return config.json's "clarify_overlay_warn_findings" — how many answered-but-not-yet-
    applied clarification findings may pile up before the dashboard suggests compacting them
    into the PRD with Apply Answers (see pending_overlay_stats in dashboard_clarify_parse.py)
    — defaulting to 25. A warning only: nothing is ever applied automatically, and carrying a
    larger overlay is legitimate — it just makes every evaluation prompt bigger."""
    return _get_positive_number(
        config, "clarify_overlay_warn_findings", DEFAULT_CONFIG["clarify_overlay_warn_findings"]
    )


def get_poll_interval_sec(config: dict) -> int | float:
    """Return config.json's "poll_interval_sec" — how often `tempa implement`'s scheduler
    loop polls for new work — defaulting to 60 (1 minute)."""
    return _get_positive_number(config, "poll_interval_sec", DEFAULT_CONFIG["poll_interval_sec"])


# Field names used to persist a resumable session id on an epic entry, per kind. Kept
# separate from the per-stage "implement" model/backend because QA sessions are resumed
# independently of the main implementation session (see tempa_session.run_qa_session).
_SESSION_ID_FIELDS = {
    "implement": ("session_id", "session_backend", "claude_session_id"),
    "qa": ("qa_session_id", "qa_session_backend", None),
}


def get_epic_session_id(epic: dict, current_backend: str, kind: str = "implement") -> str | None:
    """Return the resumable session id stored on `epic` for `kind` ("implement" | "qa"),
    but only if it was captured under `current_backend` — a session id from one CLI is
    meaningless to another, so a stage's backend switching mid-epic starts fresh instead
    of feeding a foreign id to --resume. Configs written before multi-backend support have
    no `*_backend` companion field (and, for "implement", used the legacy `claude_session_id`
    key instead of `session_id`); those are treated as backend "claude", Tempa's only
    backend at the time they were written."""
    id_key, backend_key, legacy_key = _SESSION_ID_FIELDS[kind]
    sid = epic.get(id_key) or (epic.get(legacy_key) if legacy_key else None)
    if not sid:
        return None
    stored_backend = epic.get(backend_key, "claude")
    return sid if stored_backend == current_backend else None


def set_epic_session_id(epic: dict, backend: str, session_id: str, kind: str = "implement") -> None:
    """Persist a resumable session id + the backend it was captured under onto `epic`
    (mutates in place — caller is responsible for saving config). Drops the legacy
    `claude_session_id` key for "implement" so it can't be misread as still current."""
    id_key, backend_key, legacy_key = _SESSION_ID_FIELDS[kind]
    epic[id_key] = session_id
    epic[backend_key] = backend
    if legacy_key:
        epic.pop(legacy_key, None)


def get_clarify_session_id(config: dict, current_backend: str) -> str | None:
    """Return the resumable session id of the most recent clarify EVALUATE session
    (config["clarify_session_id"]), but only if it was captured under `current_backend`
    — same reasoning as get_epic_session_id. Used by an apply pass to resume the
    evaluate session that just wrote the findings it's about to apply, instead of
    re-reading the whole PRD cold. Unlike epic session ids, this isn't namespaced by
    "kind" — apply always resumes the evaluate session specifically, never itself."""
    sid = config.get("clarify_session_id")
    if not sid:
        return None
    return sid if config.get("clarify_session_backend", "claude") == current_backend else None


def get_clarify_apply_session_id(config: dict, current_backend: str) -> str | None:
    """Return the resumable session id of the apply session's OWN most recent attempt
    (config["clarify_apply_session_id"]), captured under `current_backend`. Distinct
    from get_clarify_session_id (the evaluate session apply normally resumes): this one
    exists so that if an apply session itself gets interrupted by a usage-limit/overload
    retry mid-run (see run_with_usage_limit_retry), the retried attempt resumes THAT
    partial apply attempt instead of losing its progress and falling back to resuming
    evaluate (or starting cold) again."""
    sid = config.get("clarify_apply_session_id")
    if not sid:
        return None
    return sid if config.get("clarify_apply_session_backend", "claude") == current_backend else None
