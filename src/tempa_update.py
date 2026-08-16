"""Tempa's own self-update: `tempa version`, `tempa check-update`, `tempa update`.

Updating Tempa is the one workflow that isn't about the user's workspace at all — it reads
the VERSION file at the install root, asks GitHub for the latest published release, and (once
confirmed) unpacks that release's tempa.zip over the install folder. It lives in its own
module because nothing else in the CLI touches the network or the install directory, and
because the dashboard's Settings page needs the version/check halves too — importing them
from here keeps it clear of tempa_commands, which pulls in the whole dashboard via
dashboard_ui.

Only files that are part of the release archive are overwritten. Everything else already on
disk — `.tempa/`, `.active-workspace`, `__pycache__`, a dev checkout's `.git` — survives,
because none of it is in the archive to begin with.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from tempa_config import SCRIPT_DIR
from tempa_logging import _banner, log

GITHUB_RELEASES_API = "https://api.github.com/repos/daus95/tempa/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/daus95/tempa/releases/latest"
GITHUB_LATEST_DOWNLOAD_URL = "https://github.com/daus95/tempa/releases/latest/download/tempa.zip"


def get_local_version() -> str:
    """Read the installed Tempa version from the VERSION file at the install root
    (SCRIPT_DIR) — that file is bumped as part of cutting each GitHub release."""
    try:
        return (SCRIPT_DIR / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def print_version() -> None:
    """`tempa version` — show the locally installed Tempa version."""
    print(f"Tempa {get_local_version()}", flush=True)


def get_latest_release_version(timeout: float = 5.0) -> str | None:
    """Query GitHub for the tag of the latest published release. Returns None (rather than
    raising) on any network failure or unexpected response, since this is a best-effort
    check, not something the rest of the CLI depends on."""
    request = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tempa-cli"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    tag = data.get("tag_name", "")
    return tag.removeprefix("v") or None


def print_check_update() -> None:
    """`tempa check-update` — compare the installed version against GitHub's latest release."""
    local = get_local_version()
    _banner("CHECK FOR UPDATES")
    print(f"  Installed version : {local}", flush=True)

    latest = get_latest_release_version()
    if latest is None:
        print("  Could not reach GitHub to check the latest release (offline, or "
              "api.github.com unreachable).", flush=True)
        print(f"  Check manually: {GITHUB_RELEASES_PAGE}", flush=True)
        return

    print(f"  Latest release    : {latest}", flush=True)
    if local != "unknown" and local == latest:
        print("  You're up to date.", flush=True)
    else:
        print(f"  Update available — download: {GITHUB_LATEST_DOWNLOAD_URL}", flush=True)


def _confirm_update() -> None:
    """Ask for interactive "yes" confirmation before downloading and applying an update
    (skippable with --yes). Exits the process if not confirmed — never returns in that case."""
    if "--yes" in sys.argv:
        return
    if not sys.stdin.isatty():
        log("Aborted — confirmation required. Run in an interactive terminal, or add --yes.")
        sys.exit(1)
    try:
        answer = input('Type "yes" to download and apply this update (anything else cancels): ').strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        log("UPDATE CANCELLED — nothing was changed.")
        sys.exit(0)


def _download_release_zip(dest: Path) -> None:
    """Download the latest release's tempa.zip asset to `dest`, printing basic progress
    (a self-overwriting line on a real terminal, periodic lines otherwise)."""
    request = urllib.request.Request(GITHUB_LATEST_DOWNLOAD_URL, headers={"User-Agent": "tempa-cli"})
    is_tty = sys.stdout.isatty()
    with urllib.request.urlopen(request, timeout=30) as response, open(dest, "wb") as out_file:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if total:
                line = f"  Downloading... {downloaded * 100 // total}% ({downloaded // 1024} KB / {total // 1024} KB)"
            else:
                line = f"  Downloading... {downloaded // 1024} KB"
            if is_tty:
                print(f"\r{line}   ", end="", flush=True)
            elif downloaded // 65536 % 16 == 0:
                print(line, flush=True)
    if is_tty:
        print(flush=True)


def run_update() -> None:
    """`tempa update` — check GitHub for a newer release and, once confirmed, download and
    apply it on top of this install. Only overwrites files that are actually part of the
    release archive (tracked repo files); anything else already on disk — `.tempa/`,
    `.active-workspace`, `__pycache__`, a dev checkout's `.git`, etc. — is left untouched
    since none of those are ever included in the archive in the first place."""
    local = get_local_version()
    _banner("UPDATE TEMPA")
    print(f"  Installed version : {local}", flush=True)
    print(f"  Install location  : {SCRIPT_DIR}", flush=True)

    latest = get_latest_release_version()
    if latest is None:
        print("  Could not reach GitHub to check the latest release (offline, or "
              "api.github.com unreachable). Nothing was changed.", flush=True)
        sys.exit(1)

    print(f"  Latest release    : {latest}", flush=True)
    if local != "unknown" and local == latest:
        print("  Already up to date — nothing to do.", flush=True)
        return

    print(f"  This will download release {latest} and overwrite the matching files in "
          f"{SCRIPT_DIR}.", flush=True)
    _confirm_update()

    with tempfile.TemporaryDirectory(prefix="tempa-update-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        zip_path = tmp_path / "tempa.zip"
        log(f"Downloading release {latest}...")
        try:
            _download_release_zip(zip_path)
        except (urllib.error.URLError, OSError) as exc:
            log(f"Download failed: {exc}. Nothing was changed.")
            sys.exit(1)

        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        source_root = extract_dir / "tempa"
        if not source_root.is_dir():
            log("Unexpected archive layout (no 'tempa/' folder inside it) — aborting, "
                "nothing was changed.")
            sys.exit(1)

        shutil.copytree(source_root, SCRIPT_DIR, dirs_exist_ok=True)

    log(f"Updated to {latest}. Restart any running 'tempa dashboard' or 'tempa implement' "
        "session so it picks up the new code.")
