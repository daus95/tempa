"""Linux desktop conveniences (zenity/kdialog, xdg-open).

Native folder-picker dialog and open-in-file-manager helper used by the Home page's
working-folder actions. Best-effort by nature: unlike Windows (WinForms via PowerShell) or
macOS (osascript), there is no single native API guaranteed to exist across Linux desktop
environments, so this relies on whichever of a couple of very common command-line tools is
actually installed. Other platforms use dashboard_winui or dashboard_macui."""

from __future__ import annotations

import shutil
import subprocess

# Tried in this order: zenity ships by default on the most common GTK/GNOME-based distros
# (Ubuntu, Fedora Workstation, ...); kdialog covers KDE-based ones (Kubuntu, Fedora KDE, ...).
_PICKER_COMMANDS = (
    ("zenity", ["zenity", "--file-selection", "--directory", "--title=Select Working Folder"]),
    ("kdialog", ["kdialog", "--getexistingdirectory", ".", "Select Working Folder"]),
)


def _pick_folder_dialog() -> str | None:
    """Open a native folder-picker dialog via whichever of zenity or kdialog is installed,
    and return the selected absolute path, or None if the user cancelled (both tools exit
    non-zero on Cancel, same signal as a real error — from the caller's point of view a
    cancel and "closed the dialog" are handled identically, so this doesn't distinguish
    them either).

    Raises RuntimeError if neither tool is installed, so the caller can surface a real
    error — and point at `tempa init <path>` as the fallback — instead of silently doing
    nothing."""
    for name, cmd in _PICKER_COMMANDS:
        if shutil.which(name) is None:
            continue
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", timeout=300,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None
        path = (result.stdout or "").strip()
        return path or None
    raise RuntimeError(
        "No folder-picker dialog found (tried zenity, kdialog). Install one of them, or "
        "run `tempa init <path>` from the CLI instead."
    )


def _open_and_focus_folder(root: str) -> None:
    """Open `root` in the desktop's default file manager via `xdg-open` — used by the path
    label on the Home page's working-folder panel. `xdg-open` is the one cross-desktop
    standard for "open this in whatever the user's file manager is" on Linux, but there is
    no equally standard way to force that window to the foreground afterward (unlike
    Windows' user32 calls or macOS' `Finder ... activate`) — window focus is also commonly
    restricted by Wayland compositors for security reasons regardless of tooling — so this
    only opens the folder; it does not attempt to focus it.

    Raises RuntimeError if `xdg-open` isn't installed or reports a failure."""
    if shutil.which("xdg-open") is None:
        raise RuntimeError("xdg-open was not found; cannot open the folder in a file manager.")
    try:
        result = subprocess.run(
            ["xdg-open", root],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Timed out opening the folder.") from e
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Could not open the folder.").strip())
