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

import copy
import json
import tempfile
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

# Per-workspace state (config.json + logs/ + qa/ + verify/ + specs/) all live under this
# hidden sub-folder, INSIDE the workspace being automated — not inside Tempa's own install —
# so each workspace keeps its own config/history across switches. Until a workspace is
# active, everything falls back to this same sub-folder inside Tempa's own install (SCRIPT_DIR),
# purely as scratch space so commands like `set-model`/`test` still work pre-`init`.
TEMPA_SUBDIR_NAME = ".tempa"


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
# - clarify  : PRD clarification session (clarify)
# - plan     : epic/feature/task planning session (run automatically by implement / implement --replan)
# - implement: implementation session (implement), including QA and verify
DEFAULT_MODELS = {
    "clarify": "claude-opus-5",
    "plan": "claude-sonnet-5",
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


# Fresh-install / deleted-file fallback for load_config() below. Mirrors the shape
# documented in docs/config-json.md — every key a brand-new config.json should have,
# with no workspace linked yet (empty root) and no run history.
DEFAULT_CONFIG = {
    "models": dict(DEFAULT_MODELS),
    "backends": dict(DEFAULT_BACKENDS),
    "reasoning_efforts": dict(DEFAULT_REASONING_EFFORTS),
    "features_per_session": 3,
    "max_session_run": 30,
    "max_clarification_run": 20,
    "usage_limit_retry_wait_sec": 1800,
    "usage_limit_heartbeat_sec": 300,
    "server_overloaded_retry_wait_sec": 300,
    "poll_interval_sec": 60,
    "last_clarification_findings": {"critical": 0, "major": 0, "minor": 0},
    "last_clarification_round": 0,
    "last_clean_evaluation_at": 0,
    "last_auto_answer": 0,
    "allow_finalize_with_critical": False,
    "skip_minor_findings": True,
    "implementation_start_requirement": "no_critical_or_major",
    "epic": [],
    "workspace": dict(DEFAULT_WORKSPACE),
}


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
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


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
