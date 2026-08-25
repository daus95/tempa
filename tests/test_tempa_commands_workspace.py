"""Tests for tempa_commands.py's run_init/run_close_folder — that both feed the
recent-workspaces history (see tempa_config.record_workspace_history), which is what lets the
dashboard Home page's "recent working folders" list survive a `close-folder`, and that init
writes the workspace's .gitignore rules (see tempa_git.ensure_prd_tracked)."""

from __future__ import annotations

import argparse

import tempa_commands as tcmd
import tempa_config


def test_run_init_records_the_workspace_in_history(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))

    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(root)]


def test_run_close_folder_records_the_previously_active_workspace(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))

    tcmd.run_close_folder()

    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(root)]
    assert tempa_config.get_active_workspace_root() is None


def test_run_close_folder_with_no_active_workspace_does_not_raise(isolate_tempa_paths):
    tcmd.run_close_folder()
    assert tempa_config.read_workspace_history() == []


# ---------------------------------------------------------------------------
# run_init writes the .gitignore rules
# ---------------------------------------------------------------------------

def test_run_init_writes_the_gitignore_rules(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))

    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".tempa/*" in lines
    assert "!.tempa/specs/prd/" in lines


def test_run_init_upgrades_a_workspace_scaffolded_before_the_prd_was_tracked(
    tmp_path, isolate_tempa_paths,
):
    """Re-running init is how an existing workspace picks up the new rules — the dashboard
    shells out to it every time a folder is opened."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n.tempa/\n", encoding="utf-8")

    tcmd.run_init(argparse.Namespace(root=str(root)))

    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".tempa/" not in lines            # the blanket entry is replaced, not kept
    assert "!.tempa/specs/prd/" in lines
    assert "node_modules/" in lines


def test_run_init_twice_leaves_one_copy_of_the_rules(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))
    tcmd.run_init(argparse.Namespace(root=str(root)))

    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert text.count("!.tempa/specs/prd/") == 1
