"""Windows-only desktop conveniences (ctypes / PowerShell).

Native folder-picker dialog and bring-Explorer-window-to-front helpers used by the Home
page's working-folder actions. Windows-specific by nature; other platforms use the CLI."""

from __future__ import annotations

import ctypes
import subprocess


def _pick_folder_dialog() -> str | None:
    """Open a native Windows folder-picker dialog and return the selected absolute
    path, or None if the user cancelled. Shelled out to PowerShell (WinForms'
    FolderBrowserDialog needs an STA apartment) rather than opened in-process, since
    ThreadingHTTPServer handles each request on its own worker thread and GUI toolkits
    aren't safe to drive from there."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select Working Folder'; "
        "$f.ShowNewFolderButton = $true; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Sta", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = (result.stdout or "").strip()
    return path or None


def _find_explorer_window(folder_name: str) -> int | None:
    """Find a visible Explorer file-browser window (class "CabinetWClass") whose
    title starts with `folder_name` — Explorer titles a folder window "<name>" on
    Windows 10 but "<name> - File Explorer" on Windows 11, so match by prefix rather
    than equality. Returns the last matching HWND found (most Z-order relevant in
    practice), or None."""
    user32 = ctypes.windll.user32
    matches: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value != "CabinetWClass":
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        if title_buf.value.startswith(folder_name):
            matches.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return matches[-1] if matches else None


def _bring_window_to_front(hwnd: int) -> None:
    """Force `hwnd` to the foreground. A background process's plain SetForegroundWindow
    call is normally ignored by Windows' foreground-lock (the window just flashes in
    the taskbar instead) — tapping ALT is the standard workaround: it resets the lock
    system-wide, letting the very next SetForegroundWindow call through. The
    topmost-flash + BringWindowToTop calls are the usual companions to that trick,
    for the cases where SetForegroundWindow alone still gets ignored."""
    user32 = ctypes.windll.user32
    VK_MENU, KEYEVENTF_KEYUP, SW_RESTORE = 0x12, 0x0002, 9
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
