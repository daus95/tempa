"""Tests for tempa_git — the auto-commit behind the commit_after_qa_pass and
finalize_checkpoint_commit settings, and the .gitignore rules that decide what those commits
can actually see. Exercises a real git repo under tmp_path (git is a required tool for anyone
running Tempa, so this doesn't need mocking), and asks git itself about ignore status rather
than pattern-matching the .gitignore text."""

from __future__ import annotations

import subprocess

import tempa_git as tg


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _log_subjects(path):
    result = subprocess.run(
        ["git", "log", "--format=%s"], cwd=path,
        stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True,
    )
    return result.stdout.splitlines()


def test_commit_workspace_changes_no_root_configured():
    outcome, detail = tg.commit_workspace_changes("", "message")
    assert outcome == "skipped"
    assert "not configured" in detail


def test_commit_workspace_changes_not_a_git_repo(tmp_path):
    outcome, detail = tg.commit_workspace_changes(str(tmp_path), "message")
    assert outcome == "skipped"
    assert "not a git repository" in detail


def test_commit_workspace_changes_nothing_to_commit(tmp_path):
    _init_repo(tmp_path)
    outcome, detail = tg.commit_workspace_changes(str(tmp_path), "message")
    assert outcome == "skipped"
    assert "no changes" in detail


def test_commit_workspace_changes_commits_new_and_modified_files(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "existing.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    (tmp_path / "existing.txt").write_text("v2")
    (tmp_path / "new.txt").write_text("brand new")

    outcome, detail = tg.commit_workspace_changes(str(tmp_path), "tempa: EPIC-01 — QA passed")

    assert outcome == "committed"
    assert detail
    subjects = _log_subjects(tmp_path)
    assert subjects[0] == "tempa: EPIC-01 — QA passed"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path,
        stdout=subprocess.PIPE, text=True, check=True,
    )
    assert status.stdout.strip() == ""


def test_commit_workspace_changes_failed_add_reports_failure(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="fatal: boom")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    outcome, detail = tg.commit_workspace_changes(str(tmp_path), "message")
    assert outcome == "failed"
    assert "boom" in detail


# ---------------------------------------------------------------------------
# ensure_prd_tracked — the .gitignore rules that keep .tempa/ out of the repo
# while letting the PRD in.
# ---------------------------------------------------------------------------

def _is_ignored(repo, relative_path):
    """Ask real git, not the .gitignore text. The rules here depend on git's own
    parent-directory-exclusion behaviour, which no amount of string matching can verify."""
    return subprocess.run(
        ["git", "check-ignore", "-q", relative_path], cwd=repo,
    ).returncode == 0


def _populate_tempa_tree(root):
    for relative in (".tempa/config.json", ".tempa/logs/run.txt", ".tempa/qa/report.md",
                     ".tempa/specs/clarifications/c.md", ".tempa/specs/pbi/epics/e.md",
                     ".tempa/specs/prd/overview.md", ".tempa/specs/prd/sub/detail.md"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def test_ensure_prd_tracked_keeps_the_prd_and_ignores_the_rest(tmp_path):
    """The test that matters: everything Tempa generates stays out of the repo except the
    PRD, which is the whole point of the block."""
    _init_repo(tmp_path)
    assert tg.ensure_prd_tracked(str(tmp_path))[0] == "created"
    _populate_tempa_tree(tmp_path)

    assert not _is_ignored(tmp_path, ".tempa/specs/prd/overview.md")
    assert not _is_ignored(tmp_path, ".tempa/specs/prd/sub/detail.md")
    for ignored in (".tempa/config.json", ".tempa/logs/run.txt", ".tempa/qa/report.md",
                    ".tempa/specs/clarifications/c.md", ".tempa/specs/pbi/epics/e.md"):
        assert _is_ignored(tmp_path, ignored), f"{ignored} should be ignored"


def test_ensure_prd_tracked_makes_git_add_stage_only_the_prd(tmp_path):
    """The consequence the checkpoint commit depends on."""
    _init_repo(tmp_path)
    tg.ensure_prd_tracked(str(tmp_path))
    _populate_tempa_tree(tmp_path)

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path,
        stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True,
    ).stdout.split()

    assert sorted(staged) == [
        ".gitignore", ".tempa/specs/prd/overview.md", ".tempa/specs/prd/sub/detail.md"]


def test_ensure_prd_tracked_creates_the_file_when_missing(tmp_path):
    outcome, detail = tg.ensure_prd_tracked(str(tmp_path))
    assert outcome == "created"
    assert str(tmp_path / ".gitignore") in detail
    assert "!.tempa/specs/prd/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_ensure_prd_tracked_upgrades_a_legacy_entry_in_place(tmp_path):
    """A workspace scaffolded before this existed carries a blanket `.tempa/` line. Git will
    not descend into an excluded directory, so that line has to GO — appending `!` rules after
    it would silently do nothing."""
    (tmp_path / ".gitignore").write_text(
        "node_modules/\n*.log\n.tempa/\ndist/\n", encoding="utf-8")

    outcome, detail = tg.ensure_prd_tracked(str(tmp_path))

    assert outcome == "updated"
    assert "upgraded" in detail
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".tempa/" not in lines            # the blanket entry is gone
    assert "!.tempa/specs/prd/" in lines
    # Every unrelated entry survives, in its original order and position.
    assert lines[0] == "node_modules/" and lines[1] == "*.log" and lines[-1] == "dist/"


def test_ensure_prd_tracked_upgrades_a_legacy_entry_without_a_trailing_slash(tmp_path):
    (tmp_path / ".gitignore").write_text(".tempa\n", encoding="utf-8")

    assert tg.ensure_prd_tracked(str(tmp_path))[0] == "updated"

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".tempa" not in lines
    assert "!.tempa/specs/prd/" in lines


def test_ensure_prd_tracked_appends_when_there_is_no_tempa_entry(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    outcome, detail = tg.ensure_prd_tracked(str(tmp_path))

    assert outcome == "updated"
    assert "added" in detail
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n")
    assert "!.tempa/specs/prd/" in text.splitlines()


def test_ensure_prd_tracked_appends_a_newline_first_when_the_file_lacks_one(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/", encoding="utf-8")

    tg.ensure_prd_tracked(str(tmp_path))

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "node_modules/"
    assert "!.tempa/specs/prd/" in lines


def test_ensure_prd_tracked_is_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text(".tempa/\n", encoding="utf-8")
    assert tg.ensure_prd_tracked(str(tmp_path))[0] == "updated"
    after_first = (tmp_path / ".gitignore").read_text(encoding="utf-8")

    for _ in range(3):
        outcome, _ = tg.ensure_prd_tracked(str(tmp_path))
        assert outcome == "unchanged"

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == after_first
    assert after_first.count("!.tempa/specs/prd/") == 1


def test_ensure_prd_tracked_does_not_require_a_git_repo(tmp_path):
    """Writing the rules into a folder someone `git init`s later is harmless, and means the
    file is already right when they do."""
    assert not (tmp_path / ".git").exists()
    assert tg.ensure_prd_tracked(str(tmp_path))[0] == "created"


def test_ensure_prd_tracked_no_root_configured():
    outcome, detail = tg.ensure_prd_tracked("")
    assert outcome == "skipped"
    assert "not configured" in detail


def test_ensure_prd_tracked_reports_a_write_failure_without_raising(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(tg.Path, "write_text", boom)

    outcome, detail = tg.ensure_prd_tracked(str(tmp_path))

    assert outcome == "failed"
    assert "read-only filesystem" in detail
