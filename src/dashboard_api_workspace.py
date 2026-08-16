"""The Home page's working-folder controls plus the two Settings maintenance actions:
select/open/detach a workspace, Clear Everything, apply an update, restart the server.

What these have in common is that none of them do the work in-process — each shells out to
`tempa.py <command>`, the same way the dashboard runs clarify and implement (see
docs/architecture.md, "The CLI/dashboard boundary"). Keeping the logic in the CLI means
there is exactly one implementation of `init`, `close-folder`, `clear` and `update`, and a
failure in any of them can't take the dashboard process down with it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dashboard_config import _resolve_source_dir, _workspace_root

Response = tuple[int, dict]

TEMPA_PY = Path(__file__).resolve().parent.parent / "tempa.py"


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
    # Re-derive prd_dir/clar_dir now that workspace.root is set, so the dashboard
    # reflects the new location immediately instead of requiring a restart.
    server.prd_dir = _resolve_source_dir("prd", "prd")
    server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
    print(f"[workspace] root set to {root}")
    return 200, {"ok": True, "root": root, "output": output}


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
    server.prd_dir = _resolve_source_dir("prd", "prd")
    server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
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
