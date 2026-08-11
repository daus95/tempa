"""Tests for tempa_git.commit_workspace_changes — the git helper behind the
commit_after_qa_pass setting. Exercises a real git repo under tmp_path (git is a
required tool for anyone running Tempa, so this doesn't need mocking)."""

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
