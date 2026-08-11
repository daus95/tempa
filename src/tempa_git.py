"""Auto-commit the target workspace after an epic passes QA.

Single entry point, `commit_workspace_changes`, used by `run_qa_session`
(tempa_session.py) right after a genuine QA pass, gated by the
`commit_after_qa_pass` config setting (see `get_commit_after_qa_pass` in
tempa_config.py).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

_TIMEOUT_SECONDS = 60


def commit_workspace_changes(workspace_root: str, message: str) -> tuple[str, str]:
    """Stage and commit all changes in workspace_root.

    Returns (outcome, detail): outcome is "committed", "skipped", or "failed".
    Never raises — a workspace that isn't a git repo, or has nothing to commit,
    is a normal "skipped" outcome, not an error.
    """
    if not workspace_root:
        return "skipped", "workspace root not configured"

    root = Path(workspace_root)
    if not (root / ".git").exists():
        return "skipped", "workspace is not a git repository"

    try:
        add_result = subprocess.run(
            ["git", "add", "-A"],
            cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return "failed", f"git add failed: {e}"
    if add_result.returncode != 0:
        return "failed", f"git add failed: {add_result.stdout.strip()}"

    try:
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=root, timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return "failed", f"git diff failed: {e}"
    if diff_result.returncode == 0:
        return "skipped", "no changes to commit"

    # The message is written to a file and passed via `-F` rather than `-m <message>` on
    # the command line — on Windows, a non-ASCII argv string (e.g. an epic name with
    # non-English characters) can get mangled by argv encoding before git ever sees it;
    # a UTF-8 file read by git avoids that entirely, on every platform.
    fd, message_path = tempfile.mkstemp(prefix=".tempa-commit-msg-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(message)
        try:
            commit_result = subprocess.run(
                ["git", "commit", "-F", message_path],
                cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return "failed", f"git commit failed: {e}"
    finally:
        with contextlib.suppress(OSError):
            os.remove(message_path)

    if commit_result.returncode != 0:
        return "failed", f"git commit failed: {commit_result.stdout.strip()}"

    return "committed", commit_result.stdout.strip()
