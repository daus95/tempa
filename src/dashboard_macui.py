"""macOS-only desktop conveniences (osascript).

Native folder-picker dialog and reveal-in-Finder helper used by the Home page's
working-folder actions. macOS-specific by nature; other platforms use dashboard_winui /
dashboard_linuxui, or the CLI."""

from __future__ import annotations

import subprocess


def _pick_folder_dialog() -> str | None:
    """Open a native macOS folder-picker dialog (AppleScript's `choose folder`, run
    through osascript) and return the selected absolute path, or None if the user
    cancelled. Unlike WinForms' FolderBrowserDialog, AppleScript's dialog runs its own
    event loop inside the osascript subprocess, so it's safe to launch from any thread
    of the ThreadingHTTPServer.

    Raises RuntimeError if osascript itself isn't available, so the caller can surface
    a real error instead of treating an unusable dialog the same as a user cancelling."""
    script = 'POSIX path of (choose folder with prompt "Select Working Folder")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except FileNotFoundError as e:
        raise RuntimeError("osascript was not found; cannot open the folder picker.") from e
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None  # user cancelled: osascript exits 1 with "User canceled." on stderr
    path = (result.stdout or "").strip()
    return path or None


def _open_and_focus_folder(root: str) -> None:
    """Open `root` in Finder and bring it to the front — used by the path label on the
    Home page's working-folder panel. Uses Finder's `reveal` (rather than the `open`
    command) followed by `activate`, since `reveal` alone opens the window without
    necessarily giving it keyboard focus.

    Raises RuntimeError if osascript itself isn't available, or if Finder reports an
    error (e.g. the path was removed between the freshness check and this call)."""
    posix_path = root.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "Finder"\n'
        f'reveal POSIX file "{posix_path}"\n'
        f'activate\n'
        f"end tell"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except FileNotFoundError as e:
        raise RuntimeError("osascript was not found; cannot open the folder.") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Timed out opening the folder in Finder.") from e
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Finder could not open the folder.").strip())
