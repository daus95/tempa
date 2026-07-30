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
from pathlib import Path

# Modules live in src/, so anchor to the parent of src/ (the Tempa install root) for
# Tempa's own bootstrap files.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
WORKING_DIR = SCRIPT_DIR.parent
# Prompt templates are a resource shipped with the tool (like the dashboard's assets/), so
# they live inside src/ — anchored to this module's own folder, not SCRIPT_DIR (the root).
# One .md file per prompt (readable, editable); loaded via load_prompt() in tempa_prompts.
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"
POLL_INTERVAL_SEC = 60

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


# Fresh-install / deleted-file fallback for load_config() below. Mirrors the shape
# documented in docs/config-json.md — every key a brand-new config.json should have,
# with no workspace linked yet (empty root) and no run history.
DEFAULT_CONFIG = {
    "models": dict(DEFAULT_MODELS),
    "features_per_session": 3,
    "max_session_run": 30,
    "max_clarification_run": 20,
    "last_clarification_findings": {"critical": 0, "major": 0, "minor": 0},
    "last_auto_answer": 0,
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
