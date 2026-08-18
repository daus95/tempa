"""Thin config/workspace accessors for the dashboard.

Small read-only wrappers over the shared tempa_config module (config.json access +
workspace/sources resolution). Kept as a separate leaf so every dashboard_* module can read
config/workspace state without importing tempa.py (which would create a cycle: tempa.py
imports the dashboard). tempa_config itself imports nothing local."""

from __future__ import annotations

from pathlib import Path

import tempa_config


def _load_dashboard_config() -> dict:
    """Read config.json fresh, tolerantly ({} on any error). Delegates to
    tempa_config.read_config_safe() — the shared, stdlib-only config module both the CLI
    and the dashboard import. (The dashboard imports tempa_config, never tempa.py, so no
    import cycle: tempa.py imports this module, and tempa_config imports nothing local.)"""
    return tempa_config.read_config_safe()


def _load_clarify_applied_hashes() -> dict:
    """config.json's "clarify_applied_hashes" — {filename: content-hash-at-last-apply},
    stamped by tempa.py's _record_clarify_applied_state() right after a successful
    `tempa clarify --apply`."""
    hashes = _load_dashboard_config().get("clarify_applied_hashes")
    return hashes if isinstance(hashes, dict) else {}


def _load_clarify_file_timings() -> dict:
    """config.json's "clarify_file_timings" — {filename: {clarify_seconds, apply_seconds}},
    stamped by tempa_clarify.py right after the evaluate/apply session that produced or
    last touched each clarification file."""
    timings = _load_dashboard_config().get("clarify_file_timings")
    return timings if isinstance(timings, dict) else {}


def _workspace_initialized() -> bool:
    """Whether `tempa init` has ever been run — workspace.root is set once on first
    init and never cleared afterward, so it's the only reliable signal (specs/prd
    paths always resolve to *some* folder even when uninitialized, via WORKING_DIR
    fallbacks in tempa.py, so probing the filesystem can't distinguish the two)."""
    return bool(_load_dashboard_config().get("workspace", {}).get("root"))


def _workspace_root() -> str:
    """config.json's workspace.root, or "" if not set yet."""
    return _load_dashboard_config().get("workspace", {}).get("root", "") or ""


def _workspace_can_close() -> bool:
    """Whether the Home page's "close working folder" icon should be shown/allowed.
    Always true while a workspace is active — closing only drops the active-workspace
    pointer (see tempa_config.clear_active_workspace_root()); the workspace's own
    .tempa/ folder (epic/session state, logs, qa, specs) is never touched, so there's
    nothing left to protect against."""
    return True


def _recent_workspaces() -> list[dict]:
    """The Home page's "recent working folders" list — newest first, each entry's
    `exists` recomputed fresh from disk on every call so a folder that was deleted or
    moved since it was last opened greys out without needing a dashboard restart."""
    return [
        {
            "root": entry["root"],
            "name": Path(entry["root"]).name or entry["root"],
            "openedAt": entry["opened_at"],
            "exists": Path(entry["root"]).is_dir(),
        }
        for entry in tempa_config.read_workspace_history()
    ]


def _resolve_source_dir(source_key: str, specs_fallback: str) -> Path:
    """Resolve one `sources` folder (e.g. "prd", "clarifications") to an absolute path
    via the shared tempa_config.get_sources() — the same resolution the CLI uses, so the
    dashboard and CLI always agree. Called to refresh server.prd_dir / server.clar_dir
    right after workspace.root is set via the Home page, so the dashboard reflects the new
    location without a restart. `specs_fallback` is retained for call-site compatibility;
    get_sources already derives the per-key suffix (see DEFAULT_SOURCE_SUFFIXES)."""
    return Path(tempa_config.get_sources(_load_dashboard_config())[source_key])
