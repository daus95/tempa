"""What git sees in the target workspace: auto-commits, and the ignore rules behind them.

Two entry points, both reporting an (outcome, detail) pair and never raising — nothing here
may fail a run it is only meant to checkpoint:

- `commit_workspace_changes`, used by `run_qa_session` (tempa_session.py) right after a
  genuine QA pass, gated by `commit_after_qa_pass`, and by the `clarify --finalize`
  checkpoint (tempa_clarify.py), gated by `finalize_checkpoint_commit`.
- `ensure_prd_tracked`, which keeps `.tempa/` out of the workspace's repo while letting the
  PRD in. Called by `run_init` (tempa_commands.py) when scaffolding or reopening a workspace,
  and again right before each checkpoint commit.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

_TIMEOUT_SECONDS = 60

# The .gitignore rules that keep Tempa's own state out of the workspace's repo while leaving
# the PRD in it. `_PRD_INCLUDE_LINE` doubles as the marker that says this block is already
# present, so re-running is a no-op.
_PRD_INCLUDE_LINE = "!.tempa/specs/prd/"
_GITIGNORE_BLOCK = f"""# Tempa-managed state: config.json, logs, QA/verify reports, and the generated
# epic/clarification specs. The PRD is deliberately kept IN the repo so its history is
# diffable — git can't re-include a path whose parent directory is excluded, which is why
# this unwinds a level at a time instead of a single ".tempa/" entry.
.tempa/*
!.tempa/specs/
.tempa/specs/*
{_PRD_INCLUDE_LINE}"""

# The entry earlier versions wrote, in both the forms git treats as "ignore the directory".
# Found as a whole line, it is replaced by the block above rather than left in place: git
# will not descend into an excluded directory, so no amount of `!` lines after it could
# re-include anything underneath.
_LEGACY_ENTRIES = (".tempa/", ".tempa")


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


def ensure_prd_tracked(workspace_root: str) -> tuple[str, str]:
    """Make sure <workspace_root>/.gitignore keeps .tempa/ out of the repo but lets
    .tempa/specs/prd/ into it.

    Returns (outcome, detail): outcome is "created", "updated", "unchanged", "skipped", or
    "failed". Never raises — this runs before an unattended checkpoint commit, and a
    .gitignore that couldn't be written is a reason to log and carry on, not to lose the run.

    Deliberately does NOT require the workspace to be a git repo yet: writing the rules into
    a folder someone `git init`s later is harmless, and means the file is already right when
    they do. Only a missing root is skipped.
    """
    if not workspace_root:
        return "skipped", "workspace root not configured"

    path = Path(workspace_root) / ".gitignore"
    try:
        if not path.exists():
            path.write_text(_GITIGNORE_BLOCK + "\n", encoding="utf-8", newline="\n")
            return "created", str(path)

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if _PRD_INCLUDE_LINE in lines:
            return "unchanged", f"{path} already keeps the PRD in the repo"

        legacy_at = next(
            (i for i, line in enumerate(lines) if line.strip() in _LEGACY_ENTRIES), None)
        if legacy_at is None:
            # Nothing of ours in there yet — append, keeping whatever the user already has.
            suffix = "" if not text or text.endswith("\n") else "\n"
            path.write_text(text + suffix + _GITIGNORE_BLOCK + "\n",
                            encoding="utf-8", newline="\n")
            return "updated", f"added the Tempa block to {path}"

        # Swap the old blanket entry for the block, in place, so the rules stay where the
        # user expects them and every unrelated line is preserved exactly.
        lines[legacy_at:legacy_at + 1] = _GITIGNORE_BLOCK.split("\n")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return "updated", f"upgraded the '.tempa/' entry in {path} to keep the PRD tracked"
    except OSError as e:
        return "failed", f"could not update {path}: {e}"
