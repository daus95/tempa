"""The Home page's working-folder controls plus the two Settings maintenance actions:
select/open/detach a workspace, Clear Everything, apply an update, restart the server.

What these have in common is that none of them do the work in-process — each shells out to
`tempa.py <command>`, the same way the dashboard runs clarify and implement (see
docs/architecture.md, "The CLI/dashboard boundary"). Keeping the logic in the CLI means
there is exactly one implementation of `init`, `close-folder`, `clear` and `update`, and a
failure in any of them can't take the dashboard process down with it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from dashboard_config import _recent_workspaces, _resolve_source_dir, _workspace_root
from tempa_config import _history_key, read_workspace_history, remove_workspace_history

Response = tuple[int, dict]

TEMPA_PY = Path(__file__).resolve().parent.parent / "tempa.py"

# Windows device names are reserved regardless of extension (e.g. "CON.txt" is just as
# invalid as "CON") and matching is case-insensitive. Harmless-but-consistent to reject
# these on every platform rather than branching on sys.platform, since a folder name
# that's invalid on Windows is a folder no cross-platform team wants anyway.
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
})
_WINDOWS_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def _run_tempa(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run `tempa.py <args>` to completion, capturing its combined output. stdin is
    /dev/null: these run unattended, so a command that decided to prompt must fail rather
    than hang the request."""
    return subprocess.run(
        [sys.executable, str(TEMPA_PY), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def _refresh_source_dirs(server) -> None:
    """Re-derive server.prd_dir/clar_dir/epics_dir now that workspace.root has changed
    (set or cleared), so the dashboard reflects the new location immediately instead of
    requiring a restart. Shared by every workspace-mutating handler below."""
    server.prd_dir = _resolve_source_dir("prd", "prd")
    server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
    server.epics_dir = _resolve_source_dir("epics", "pbi/epics")


def _validate_new_folder_name(name: str) -> str | None:
    """Return an error message if `name` can't be used as a single new folder segment
    under a chosen parent, or None if it's fine. Deliberately stricter than the local
    filesystem might require (e.g. rejecting Windows-reserved names on any platform) so a
    folder created here is never one that's awkward to move to Windows later."""
    if not name or not name.strip():
        return "Enter a folder name."
    if name != name.strip():
        return "The name can't start or end with whitespace."
    if name in (".", ".."):
        return "That name is reserved."
    if "/" in name or "\\" in name:
        return "The name can't contain a path separator."
    if _WINDOWS_INVALID_CHARS.search(name):
        return 'The name can\'t contain any of: < > : " | ? *'
    if name.endswith(".") or name.endswith(" "):
        return "The name can't end with a period or space."
    if name.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        return f'"{name}" is a reserved name on Windows — choose a different name.'
    if len(name) > 100:
        return "The name is too long (100 characters max)."
    return None


def clear_everything(clarify_running: bool, implement_running: bool) -> Response:
    if clarify_running or implement_running:
        return 409, {
            "ok": False,
            "error": "Cannot clear while a clarify or implementation run is in progress.",
        }
    try:
        result = _run_tempa(["clear", "--yes"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not run clear: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Clear failed (exit code {result.returncode})."}
    return 200, {"ok": True, "output": output}


def init_workspace(server, pick_folder_dialog) -> Response:
    """"Select Working Folder" on the Home page: open a native folder picker, then run
    `tempa.py init <folder>` (same as the CLI) to make it the active workspace — pointing
    Tempa at `<folder>/.tempa/config.json` (created fresh, or loaded as-is if this folder
    was used before) — and scaffold the default working folders under it.

    A picker that isn't available, or that the user cancelled, is answered with a 200 and
    `ok: false`: nothing went wrong, there is just nothing to do.
    """
    if pick_folder_dialog is None:
        return 200, {
            "ok": False,
            "error": "The folder picker isn't available on this platform. Run "
                     "`tempa init <path>` from the CLI, then reload the dashboard.",
        }
    try:
        root = pick_folder_dialog()
    except RuntimeError as e:
        return 200, {"ok": False, "error": str(e)}
    if root is None:
        return 200, {"ok": False, "cancelled": True}
    try:
        result = _run_tempa(["init", root], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not initialize: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Init failed (exit code {result.returncode})."}
    _refresh_source_dirs(server)
    print(f"[workspace] root set to {root}")
    return 200, {"ok": True, "root": root, "output": output}


def open_recent_workspace(server, payload: dict | None) -> Response:
    """A row in the Home page's "recent working folders" list. Only ever re-inits a path
    that is currently present in the recent-workspaces history — the browser can request
    reopening a folder Tempa already knows about, never an arbitrary filesystem path —
    then runs `tempa.py init <path>` exactly like init_workspace() does after its picker."""
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return 400, {"ok": False, "error": "Missing folder path."}
    key = _history_key(path)
    if not any(_history_key(e["root"]) == key for e in read_workspace_history()):
        return 404, {"ok": False, "error": "That folder isn't in the recent list."}
    try:
        result = _run_tempa(["init", path], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not initialize: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Init failed (exit code {result.returncode})."}
    _refresh_source_dirs(server)
    print(f"[workspace] root set to {path}")
    return 200, {"ok": True, "root": path, "output": output}


def pick_parent_folder(pick_folder_dialog) -> Response:
    """"Create New Working Folder" step 1: open the native picker to choose where the new
    folder should be created, without initializing anything yet — step 2 (create_workspace)
    is a separate request once the user has also named it."""
    if pick_folder_dialog is None:
        return 200, {
            "ok": False,
            "error": "The folder picker isn't available on this platform. Run "
                     "`tempa init <path>` from the CLI, then reload the dashboard.",
        }
    try:
        path = pick_folder_dialog()
    except RuntimeError as e:
        return 200, {"ok": False, "error": str(e)}
    if path is None:
        return 200, {"ok": False, "cancelled": True}
    return 200, {"ok": True, "path": path}


def create_workspace(server, payload: dict | None) -> Response:
    """"Create New Working Folder" step 2: `parent` (an existing absolute directory picked
    in step 1) plus a new `name` typed by the user. Validated here so a bad name never
    reaches `tempa.py init`; the actual folder creation is still entirely init's job — it
    already creates a missing root and scaffolds it, so there is nothing to duplicate."""
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    parent = payload.get("parent")
    name = payload.get("name")
    if not isinstance(parent, str) or not parent:
        return 400, {"ok": False, "error": "Missing parent folder."}
    if not isinstance(name, str):
        return 400, {"ok": False, "error": "Missing folder name."}
    parent_path = Path(parent)
    if not parent_path.is_absolute() or not parent_path.is_dir():
        return 400, {"ok": False, "error": "The chosen location no longer exists."}
    error = _validate_new_folder_name(name)
    if error:
        return 400, {"ok": False, "error": error}
    target = parent_path / name
    if target.exists():
        return 409, {
            "ok": False,
            "error": "A folder with that name already exists — use Select Working Folder to open it.",
        }
    try:
        result = _run_tempa(["init", str(target)], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not initialize: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Init failed (exit code {result.returncode})."}
    _refresh_source_dirs(server)
    print(f"[workspace] root set to {target}")
    return 200, {"ok": True, "root": str(target), "output": output}


def remove_recent_workspace(payload: dict | None) -> Response:
    """The "✕" on a recent-working-folder row — drops it from the history list only; the
    folder itself, and any active workspace, are untouched. Returns the refreshed list so
    the row disappears without a separate /api/tree round trip."""
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return 400, {"ok": False, "error": "Missing folder path."}
    remove_workspace_history(path)
    return 200, {"ok": True, "recent": _recent_workspaces()}


def open_workspace_folder(open_and_focus_folder) -> Response:
    """Open workspace.root in the OS file manager (Explorer/Finder/xdg-open) and, on
    Windows/macOS, bring it to the front — used by the path label on the Home page's
    working-folder panel. Unsupported only if the platform isn't recognized, or (on
    Linux) if none of the relied-upon command-line tools are installed (see
    _open_and_focus_folder)."""
    root = _workspace_root()
    if not root or not Path(root).is_dir():
        return 404, {"ok": False, "error": "Working folder not found on disk."}
    if open_and_focus_folder is None:
        return 200, {
            "ok": False,
            "error": "Opening the working folder in a file manager isn't supported on this platform.",
        }
    try:
        open_and_focus_folder(root)
    except (OSError, RuntimeError) as e:
        return 500, {"ok": False, "error": f"Could not open folder: {e}"}
    return 200, {"ok": True}


def close_workspace(server) -> Response:
    """Detach the active workspace — the "✕" icon next to the working-folder path.
    Shells out to `tempa.py close-folder` (same subprocess pattern as init/clear) so
    the pointer-clearing logic stays in one place. Always available while a workspace
    is active — it only drops the active-workspace pointer, the workspace's own
    .tempa/ folder is never touched, so there's nothing to gate on."""
    try:
        result = _run_tempa(["close-folder"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not close working folder: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Close failed (exit code {result.returncode})."}
    _refresh_source_dirs(server)
    print("[workspace] root cleared")
    return 200, {"ok": True}


def run_update(clarify_running: bool, implement_running: bool) -> Response:
    """Apply the latest GitHub release on top of this install. Delegates to
    `tempa.py update --yes` in a subprocess (rather than calling tempa_update.run_update()
    in-process) because that function can sys.exit() on failure paths — isolating it in a
    child process keeps a failed update from taking the dashboard server down with it."""
    if clarify_running or implement_running:
        return 409, {
            "ok": False,
            "error": "Cannot update while a clarify or implementation run is in progress.",
        }
    try:
        result = _run_tempa(["update", "--yes"], timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, {"ok": False, "error": f"Could not run update: {e}"}
    output = result.stdout or ""
    if result.returncode != 0:
        return 500, {"ok": False, "error": output.strip() or f"Update failed (exit code {result.returncode})."}
    import tempa_update
    return 200, {"ok": True, "output": output, "version": tempa_update.get_local_version()}


def relaunch_server(port: int, clarify_running: bool, implement_running: bool) -> Response:
    """Spawn a replacement dashboard process bound to the same port, so the browser can
    reload the same URL once it's back up. Detached, because it must outlive this process
    exiting, and spawned *before* this server gives up its socket — the new process's own
    bind-retry loop (run_dashboard()'s `port` handling in dashboard_ui.py) bridges the brief
    window where both are momentarily contending for the port.

    Shutting this server down is the caller's job, once the response has been sent."""
    if clarify_running or implement_running:
        return 409, {
            "ok": False,
            "error": "Cannot restart while a clarify or implementation run is in progress.",
        }
    cmd = [sys.executable, str(TEMPA_PY), "dashboard", "--port", str(port), "--no-browser"]
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **popen_kwargs,
        )
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not start the new server process: {e}"}
    return 200, {"ok": True, "port": port}
